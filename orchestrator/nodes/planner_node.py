"""Узел планирования последовательности вызовов инструментов."""

from __future__ import annotations

from datetime import date, timedelta
import json
import re
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field
import structlog

from config.settings import get_settings
from orchestrator.prompts import PLANNER_SYSTEM_PROMPT

log = structlog.get_logger()


class PlanStepModel(BaseModel):
    """Схема одного шага плана."""

    step_number: int
    description: str
    target_server: Literal["market_executor", "news_executor", "analytics_executor", "summarizer"]
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)


class PlanSchema(BaseModel):
    """Схема ответа планировщика."""

    steps: list[PlanStepModel]
    reasoning: str


PORTFOLIO_RE = re.compile(r"\b[\w-]*portfolio[\w-]*\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"-?\d+(?:[.,]\d+)?\s*%")
SCENARIO_ALIASES = {
    "market_downturn": "index_drop",
    "imoex_drop": "index_drop",
    "rate_up": "rate_hike",
}
ALLOWED_TOOLS_BY_TARGET = {
    "market_executor": {"get_stock_quote", "get_candles", "get_board_securities", "get_index_analytics", "get_bond_data"},
    "news_executor": {"fetch_news", "get_cb_key_rate", "get_market_sentiment", "get_macro_calendar"},
    "analytics_executor": {
        "get_portfolio_summary",
        "calculate_risk_metrics",
        "run_stress_test",
        "execute_analytics_query",
    },
    "summarizer": {"summarize"},
}


def _detect_portfolio_id(query: str) -> str:
    """Извлекает portfolio_id из текста запроса."""
    match = PORTFOLIO_RE.search(query)
    if match:
        return match.group(0)
    return "demo_portfolio"


def _is_stress_intent(query: str) -> bool:
    """Определяет стресс-сценарий по ключевым маркерам в тексте запроса."""
    lowered = query.lower()
    if any(keyword in lowered for keyword in {"стресс", "stress", "сценар", "шок", "паден"}):
        return True
    has_percent = bool(PERCENT_RE.search(lowered))
    has_index = any(keyword in lowered for keyword in {"imoex", "rtsi", "rgbi", "индекс"})
    return has_percent and has_index


def _normalize_analytics_tool_args(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_query: str,
) -> dict[str, Any]:
    """Нормализует аргументы analytics-инструментов к контракту MCP."""
    normalized = dict(tool_args)

    # Унифицируем ключ portfolio_id.
    portfolio_id = (
        normalized.get("portfolio_id")
        or normalized.get("portfolio_name")
        or normalized.get("name")
        or _detect_portfolio_id(user_query)
    )
    portfolio_id = str(portfolio_id).strip() if portfolio_id else "demo_portfolio"
    normalized["portfolio_id"] = portfolio_id
    normalized.pop("portfolio_name", None)
    normalized.pop("name", None)
    normalized.pop("tickers", None)

    if tool_name == "get_portfolio_summary":
        return {"portfolio_id": portfolio_id}

    if tool_name == "calculate_risk_metrics":
        confidence = normalized.get("confidence", 0.95)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.95
        if confidence_value <= 0 or confidence_value >= 1:
            confidence_value = 0.95
        return {"portfolio_id": portfolio_id, "confidence": confidence_value}

    if tool_name == "run_stress_test":
        scenario = str(normalized.get("scenario", "index_drop")).strip().lower()
        scenario = SCENARIO_ALIASES.get(scenario, scenario)
        if scenario not in {"index_drop", "rate_hike", "sector_decline"}:
            scenario = "index_drop"

        magnitude_raw = normalized.get("magnitude", 20.0 if scenario == "index_drop" else 2.0)
        try:
            magnitude = float(magnitude_raw)
        except (TypeError, ValueError):
            magnitude = 20.0 if scenario == "index_drop" else 2.0

        payload: dict[str, Any] = {
            "portfolio_id": portfolio_id,
            "scenario": scenario,
            "magnitude": magnitude,
        }
        if scenario == "sector_decline":
            target_sector = normalized.get("target_sector") or normalized.get("sector")
            if isinstance(target_sector, str) and target_sector.strip():
                payload["target_sector"] = target_sector.strip()
        return payload

    return normalized


def _normalize_news_tool_args(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_query: str,
    extracted_tickers: list[str],
) -> dict[str, Any]:
    """Нормализует аргументы news-инструментов."""
    normalized = dict(tool_args)
    fallback_ticker = extracted_tickers[0] if extracted_tickers else "SBER"
    if tool_name == "fetch_news":
        query = normalized.get("query") or normalized.get("ticker") or fallback_ticker or user_query
        limit = normalized.get("limit", 10)
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 10
        if limit_value <= 0:
            limit_value = 10
        if limit_value > 100:
            limit_value = 100
        payload: dict[str, Any] = {"query": str(query).strip() or fallback_ticker, "limit": limit_value}
        sources = normalized.get("sources")
        if isinstance(sources, list):
            payload["sources"] = [str(item).strip().lower() for item in sources if str(item).strip()]
        return payload
    if tool_name == "get_market_sentiment":
        ticker = normalized.get("ticker") or normalized.get("query") or fallback_ticker
        return {"ticker": str(ticker).strip().upper() or fallback_ticker}
    if tool_name == "get_macro_calendar":
        date_from = str(normalized.get("date_from") or "2026-01-01")
        date_to = str(normalized.get("date_to") or "2026-12-31")
        return {"date_from": date_from, "date_to": date_to}
    return normalized


def _normalize_market_tool_args(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    extracted_tickers: list[str],
) -> dict[str, Any]:
    """Нормализует аргументы market-инструментов."""
    normalized = dict(tool_args)
    fallback_ticker = extracted_tickers[0] if extracted_tickers else "SBER"
    if tool_name == "get_stock_quote":
        ticker = normalized.get("ticker") or normalized.get("secid") or fallback_ticker
        return {"ticker": str(ticker).strip().upper() or fallback_ticker}
    if tool_name == "get_candles":
        ticker = normalized.get("ticker") or normalized.get("secid") or fallback_ticker
        interval_raw = normalized.get("interval", 24)
        interval_map = {"1m": 1, "10m": 10, "1h": 60, "1d": 24, "1w": 7, "d": 24, "w": 7}
        interval = 24
        if isinstance(interval_raw, str):
            normalized_interval_raw = interval_raw.strip().lower()
            if normalized_interval_raw in interval_map:
                interval = interval_map[normalized_interval_raw]
            else:
                try:
                    interval = int(normalized_interval_raw)
                except ValueError:
                    interval = 24
        elif isinstance(interval_raw, (int, float)):
            interval = int(interval_raw)
        if interval not in {1, 10, 60, 24, 7}:
            interval = 24

        date_from = normalized.get("date_from")
        date_to = normalized.get("date_to")
        if not isinstance(date_from, str) or not date_from.strip():
            date_from = (date.today() - timedelta(days=30)).isoformat()
        if not isinstance(date_to, str) or not date_to.strip():
            date_to = date.today().isoformat()
        return {
            "ticker": str(ticker).strip().upper() or fallback_ticker,
            "date_from": str(date_from).strip(),
            "date_to": str(date_to).strip(),
            "interval": interval,
        }
    return normalized


def _sanitize_plan_steps(
    *,
    steps: list[dict[str, Any]],
    user_query: str,
    extracted_tickers: list[str],
) -> list[dict[str, Any]]:
    """Применяет post-validation для шагов плана после LLM/fallback."""
    sanitized: list[dict[str, Any]] = []
    for step in steps:
        normalized_step = dict(step)
        tool_name = str(normalized_step.get("tool_name", ""))
        target = str(normalized_step.get("target_server", ""))
        if target not in {"market_executor", "news_executor", "analytics_executor", "summarizer"}:
            continue
        allowed_tools = ALLOWED_TOOLS_BY_TARGET[target]
        if tool_name not in allowed_tools:
            if target == "summarizer":
                tool_name = "summarize"
            else:
                continue
        tool_args = dict(normalized_step.get("tool_args", {}))
        if target == "market_executor":
            tool_args = _normalize_market_tool_args(
                tool_name=tool_name,
                tool_args=tool_args,
                extracted_tickers=extracted_tickers,
            )
        elif target == "news_executor":
            tool_args = _normalize_news_tool_args(
                tool_name=tool_name,
                tool_args=tool_args,
                user_query=user_query,
                extracted_tickers=extracted_tickers,
            )
        elif target == "analytics_executor":
            tool_args = _normalize_analytics_tool_args(
                tool_name=tool_name,
                tool_args=tool_args,
                user_query=user_query,
            )
        elif target == "summarizer":
            tool_args = {}
            tool_name = "summarize"

        normalized_step["tool_name"] = tool_name
        normalized_step["tool_args"] = tool_args
        sanitized.append(normalized_step)

    if not sanitized or sanitized[-1].get("target_server") != "summarizer":
        sanitized.append(
            {
                "step_number": len(sanitized) + 1,
                "description": "Суммаризировать результаты.",
                "target_server": "summarizer",
                "tool_name": "summarize",
                "tool_args": {},
            }
        )
    for index, step in enumerate(sanitized, start=1):
        step["step_number"] = index
    log.info(
        "planner_plan_sanitized",
        steps_count=len(sanitized),
        targets=[step.get("target_server") for step in sanitized],
        tools=[step.get("tool_name") for step in sanitized],
    )
    return sanitized


def _build_fallback_plan(state: dict[str, Any]) -> PlanSchema:
    """Формирует детерминированный fallback-план без LLM."""
    query = str(state.get("user_query", ""))
    query_type = str(state.get("query_type", "complex"))
    tickers = list(state.get("extracted_tickers", []))
    first_ticker = tickers[0] if tickers else "SBER"
    portfolio_id = _detect_portfolio_id(query)

    steps: list[PlanStepModel] = []
    step_counter = 1

    if query_type in {"market_monitor", "complex"}:
        if "индекс" in query.lower() or "imoex" in query.lower() or "rtsi" in query.lower():
            index = "IMOEX"
            if "rtsi" in query.lower():
                index = "RTSI"
            if "rgbi" in query.lower():
                index = "RGBI"
            steps.append(
                PlanStepModel(
                    step_number=step_counter,
                    description="Получить аналитику индекса.",
                    target_server="market_executor",
                    tool_name="get_index_analytics",
                    tool_args={"index": index},
                )
            )
        else:
            steps.append(
                PlanStepModel(
                    step_number=step_counter,
                    description="Получить текущую котировку инструмента.",
                    target_server="market_executor",
                    tool_name="get_stock_quote",
                    tool_args={"ticker": first_ticker},
                )
            )
        step_counter += 1

    if query_type in {"news_analysis", "complex"}:
        steps.append(
            PlanStepModel(
                step_number=step_counter,
                description="Получить релевантные новости.",
                target_server="news_executor",
                tool_name="fetch_news",
                tool_args={"query": first_ticker, "limit": 10},
            )
        )
        step_counter += 1

        if tickers and query_type == "news_analysis":
            steps.append(
                PlanStepModel(
                    step_number=step_counter,
                    description="Рассчитать агрегированную тональность по тикеру.",
                    target_server="news_executor",
                    tool_name="get_market_sentiment",
                    tool_args={"ticker": first_ticker},
                )
            )
            step_counter += 1

    if query_type in {"risk_assessment", "complex"}:
        if _is_stress_intent(query):
            scenario = "index_drop"
            magnitude = 20.0
            if "ставк" in query.lower():
                scenario = "rate_hike"
                magnitude = 2.0
            if "сектор" in query.lower():
                scenario = "sector_decline"
                magnitude = 15.0
            steps.append(
                PlanStepModel(
                    step_number=step_counter,
                    description="Провести стресс-тестирование портфеля.",
                    target_server="analytics_executor",
                    tool_name="run_stress_test",
                    tool_args={
                        "portfolio_id": portfolio_id,
                        "scenario": scenario,
                        "magnitude": magnitude,
                    },
                )
            )
        else:
            steps.append(
                PlanStepModel(
                    step_number=step_counter,
                    description="Рассчитать риск-метрики портфеля.",
                    target_server="analytics_executor",
                    tool_name="calculate_risk_metrics",
                    tool_args={"portfolio_id": portfolio_id, "confidence": 0.95},
                )
            )
        step_counter += 1

    steps.append(
        PlanStepModel(
            step_number=step_counter,
            description="Суммаризировать результаты.",
            target_server="summarizer",
            tool_name="summarize",
            tool_args={},
        )
    )
    return PlanSchema(steps=steps, reasoning="План сформирован детерминированными правилами.")


def _try_llm_plan(state: dict[str, Any]) -> PlanSchema | None:
    """Пытается получить structured plan через LLM."""
    settings = get_settings()
    if not settings.yandex_cloud_api_key or not settings.yandex_cloud_folder:
        log.info("planner_llm_disabled_missing_credentials")
        return None

    client = OpenAI(
        api_key=settings.yandex_cloud_api_key,
        base_url=settings.llm_base_url,
        project=settings.yandex_cloud_folder,
    )
    model_name = f"gpt://{settings.yandex_cloud_folder}/{settings.yandex_cloud_model}"
    prompt_input = json.dumps(
        {
            "user_query": state.get("user_query", ""),
            "query_type": state.get("query_type", "complex"),
            "extracted_tickers": state.get("extracted_tickers", []),
        },
        ensure_ascii=False,
    )
    response = client.responses.create(
        model=model_name,
        temperature=0.1,
        instructions=PLANNER_SYSTEM_PROMPT,
        input=prompt_input,
        max_output_tokens=1200,
    )
    raw_text = response.output_text
    if not raw_text:
        log.warning("planner_llm_empty_response")
        return None
    payload = json.loads(raw_text)
    log.info("planner_llm_plan_received", steps_count=len(payload.get("steps", [])))
    return PlanSchema.model_validate(payload)


def route_planner(state: dict[str, Any]) -> str:
    """Функция маршрутизации planner узла."""
    next_node = state.get("next_node")
    if next_node in {"market_executor", "news_executor", "analytics_executor", "summarizer"}:
        return str(next_node)
    return "summarizer"


async def planner_node(state: dict[str, Any]) -> dict[str, Any]:
    """Генерирует план и выбирает следующий узел исполнения."""
    settings = get_settings()
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    log.info(
        "planner_node_started",
        current_step=current_step,
        existing_plan_steps=len(plan),
        error_count=int(state.get("error_count", 0)),
        query_type=state.get("query_type"),
    )
    if int(state.get("error_count", 0)) >= settings.max_error_count:
        log.warning("planner_node_max_errors_reached", max_error_count=settings.max_error_count)
        return {
            "next_node": "summarizer",
            "warnings": ["Достигнут лимит ошибок оркестратора, включена безопасная деградация."],
        }

    if not plan:
        if current_step > 0:
            return {"next_node": "summarizer"}
        llm_plan = None
        try:
            llm_plan = _try_llm_plan(state)
        except Exception:
            llm_plan = None
            log.warning("planner_llm_plan_failed_fallback")

        schema = llm_plan or _build_fallback_plan(state)
        log.info("planner_plan_source_selected", source="llm" if llm_plan else "fallback")
        normalized_plan = [item.model_dump() for item in schema.steps]
        normalized_plan = _sanitize_plan_steps(
            steps=normalized_plan,
            user_query=str(state.get("user_query", "")),
            extracted_tickers=list(state.get("extracted_tickers", [])),
        )
        if len(normalized_plan) > 10:
            normalized_plan = normalized_plan[:10]
        next_node = normalized_plan[0]["target_server"] if normalized_plan else "summarizer"
        log.info("planner_plan_built", next_node=next_node, steps_count=len(normalized_plan))
        return {
            "plan": normalized_plan,
            "current_step": 0,
            "next_node": next_node,
        }

    if current_step >= len(plan) or current_step >= 10:
        log.info("planner_node_route_summarizer_end_of_plan", current_step=current_step, plan_steps=len(plan))
        return {"next_node": "summarizer"}

    step = plan[current_step]
    target = step.get("target_server", "summarizer")
    if target not in {"market_executor", "news_executor", "analytics_executor", "summarizer"}:
        target = "summarizer"
    log.info(
        "planner_node_routed",
        current_step=current_step,
        target=target,
        tool_name=step.get("tool_name"),
    )
    return {"next_node": target}

