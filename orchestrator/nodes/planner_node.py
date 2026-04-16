"""Узел планирования последовательности вызовов инструментов."""

from __future__ import annotations

from datetime import date, timedelta
import json
import re
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
import structlog

from config.settings import get_settings
from orchestrator.nodes.input_node import (
    infer_sector_stress_hint,
    is_explicit_market_history_intent,
    is_investment_decision_intent,
    is_stress_intent,
)
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
PORTFOLIO_PLACEHOLDER_RE = re.compile(r"^[<{[]?\s*portfolio_id\s*[>\]}]?$", re.IGNORECASE)
SCENARIO_ALIASES = {
    "market_downturn": "index_drop",
    "imoex_drop": "index_drop",
    "rate_up": "rate_hike",
}
INDEX_ALIASES = {
    "MOEX": "IMOEX",
    "MICEX": "IMOEX",
}
ALLOWED_INDEXES = {"IMOEX", "RTSI", "RGBI"}
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


def coerce_stress_magnitude_percent_points(scenario: str, magnitude: float) -> float:
    """Приводит magnitude к процентным пунктам, как в stress_tester (20 => падение на 20%).

    Модели часто передают долю (0.2) вместо процентных пунктов (20).
    """
    normalized = scenario.strip().lower()
    if normalized not in {"index_drop", "sector_decline", "rate_hike"}:
        return magnitude
    if 0 < magnitude < 1:
        return magnitude * 100.0
    return magnitude


def _compact_plan_for_log(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Подготавливает компактный вид плана для логов (с аргументами инструментов)."""
    compact: list[dict[str, Any]] = []
    for step in steps:
        compact.append(
            {
                "step_number": step.get("step_number"),
                "target_server": step.get("target_server"),
                "tool_name": step.get("tool_name"),
                "tool_args": dict(step.get("tool_args", {})),
            }
        )
    return compact


def _detect_portfolio_id(query: str) -> str:
    """Извлекает portfolio_id из текста запроса."""
    match = PORTFOLIO_RE.search(query)
    if match:
        return match.group(0)
    return "demo_portfolio"


def _normalize_portfolio_id_value(raw_value: Any, user_query: str) -> str:
    """Нормализует portfolio_id: плейсхолдеры заменяются на стандартный id."""
    default_portfolio_id = _detect_portfolio_id(user_query)
    if raw_value is None:
        return default_portfolio_id
    value = str(raw_value).strip()
    if not value:
        return default_portfolio_id
    if PORTFOLIO_PLACEHOLDER_RE.fullmatch(value):
        return default_portfolio_id
    if value.casefold() in {"portfolio_id", "<id>", "{id}", "[id]"}:
        return default_portfolio_id
    return value


def _extract_percent_value(query: str, default: float) -> float:
    """Извлекает первое процентное значение из запроса."""
    match = PERCENT_RE.search(query)
    if not match:
        return default
    raw = match.group(0).replace("%", "").replace(",", ".").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _is_stress_intent(query: str) -> bool:
    """Определяет стресс-сценарий по ключевым маркерам в тексте запроса."""
    return is_stress_intent(query)


def infer_sector_decline_hint_from_user_query(user_query: str) -> str | None:
    """Подсказка target_sector для sector_decline, если LLM её не передал (эмитент в тексте).

    Строка передаётся в analytics/stress_tester и сопоставляется с тикерами через _ISSUER_HINT_TO_TICKERS.
    """
    lowered = user_query.casefold()
    if "яндекс" in lowered or "yandex" in lowered:
        return "яндекс"
    if "газпром" in lowered or "gazprom" in lowered:
        return "газпром"
    if "сбер" in lowered or re.search(r"\bsber\b", lowered):
        return "сбер"
    if "лукойл" in lowered or "lukoil" in lowered:
        return "лукойл"
    if "роснефт" in lowered:
        return "роснефть"
    if "норникель" in lowered or "nornickel" in lowered:
        return "норникель"
    sector_hint = infer_sector_stress_hint(user_query)
    if sector_hint:
        return sector_hint
    if "финансов" in lowered and ("просд" in lowered or "просяд" in lowered or "просед" in lowered):
        return "финансы"
    return None


def _normalize_analytics_tool_args(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_query: str,
) -> dict[str, Any]:
    """Нормализует аргументы analytics-инструментов к контракту MCP."""
    normalized = dict(tool_args)

    # Унифицируем ключ portfolio_id.
    raw_portfolio_id = (
        normalized.get("portfolio_id")
        or normalized.get("portfolio_name")
        or normalized.get("name")
    )
    portfolio_id = _normalize_portfolio_id_value(raw_portfolio_id, user_query)
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
        magnitude = coerce_stress_magnitude_percent_points(scenario, magnitude)

        payload: dict[str, Any] = {
            "portfolio_id": portfolio_id,
            "scenario": scenario,
            "magnitude": magnitude,
        }
        if scenario == "sector_decline":
            target_sector = normalized.get("target_sector") or normalized.get("sector")
            if not (isinstance(target_sector, str) and target_sector.strip()):
                inferred_sector = infer_sector_stress_hint(user_query)
                if inferred_sector:
                    target_sector = inferred_sector
            if not (isinstance(target_sector, str) and target_sector.strip()):
                inferred = infer_sector_decline_hint_from_user_query(user_query)
                if inferred:
                    target_sector = inferred
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
    fallback_ticker = extracted_tickers[0] if extracted_tickers else None
    if tool_name == "fetch_news":
        query = normalized.get("query") or normalized.get("ticker") or user_query or fallback_ticker or "рынок"
        limit = normalized.get("limit", 10)
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            limit_value = 10
        if limit_value <= 0:
            limit_value = 10
        if limit_value > 100:
            limit_value = 100
        payload: dict[str, Any] = {"query": str(query).strip() or "рынок", "limit": limit_value}
        sources = normalized.get("sources")
        if isinstance(sources, list):
            payload["sources"] = [str(item).strip().lower() for item in sources if str(item).strip()]
        return payload
    if tool_name == "get_market_sentiment":
        ticker = normalized.get("ticker") or normalized.get("query") or fallback_ticker or "SBER"
        return {"ticker": str(ticker).strip().upper() or "SBER"}
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
    if tool_name == "get_index_analytics":
        index_raw = str(normalized.get("index") or "IMOEX").strip().upper()
        index = INDEX_ALIASES.get(index_raw, index_raw)
        if index not in ALLOWED_INDEXES:
            index = "IMOEX"
        return {"index": index}
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
    query_type: str = "complex",
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

    if is_investment_decision_intent(user_query) and not is_explicit_market_history_intent(user_query):
        before = len(sanitized)
        sanitized = [step for step in sanitized if str(step.get("tool_name", "")) != "get_candles"]
        if len(sanitized) < before:
            log.info(
                "planner_stripped_optional_candles",
                user_query_preview=user_query[:120],
                steps_before=before,
                steps_after=len(sanitized),
            )

    if query_type == "portfolio_holdings":
        before_ph = len(sanitized)
        sanitized = [
            step
            for step in sanitized
            if str(step.get("tool_name", "")) not in {"calculate_risk_metrics", "run_stress_test"}
        ]
        if len(sanitized) < before_ph:
            log.info(
                "planner_stripped_risk_tools_for_holdings_query",
                user_query_preview=user_query[:120],
                steps_before=before_ph,
                steps_after=len(sanitized),
            )
        has_summary = any(str(step.get("tool_name", "")) == "get_portfolio_summary" for step in sanitized)
        if not has_summary:
            pid = _detect_portfolio_id(user_query)
            summary_args = _normalize_analytics_tool_args(
                tool_name="get_portfolio_summary",
                tool_args={"portfolio_id": pid},
                user_query=user_query,
            )
            sanitized.insert(
                0,
                {
                    "step_number": 0,
                    "description": "Получить сводку портфеля (позиции, веса).",
                    "target_server": "analytics_executor",
                    "tool_name": "get_portfolio_summary",
                    "tool_args": summary_args,
                },
            )

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


def _plan_contract_status(steps: list[dict[str, Any]], query_type: str) -> tuple[bool, str]:
    """Проверяет минимальный контракт плана для типа запроса."""
    tools = [str(step.get("tool_name", "")) for step in steps]
    targets = [str(step.get("target_server", "")) for step in steps]

    if query_type == "risk_assessment":
        has_risk_analytics = any(
            target == "analytics_executor" and tool in {"run_stress_test", "calculate_risk_metrics"}
            for target, tool in zip(targets, tools, strict=False)
        )
        if not has_risk_analytics:
            return False, "risk_missing_analytics"
        return True, "ok"

    if query_type == "news_analysis":
        has_news_tool = any(target == "news_executor" for target in targets)
        if not has_news_tool:
            return False, "news_missing_news_tool"
        return True, "ok"

    if query_type == "portfolio_holdings":
        has_summary = "get_portfolio_summary" in tools
        has_risk_tools = any(tool in {"run_stress_test", "calculate_risk_metrics"} for tool in tools)
        if not has_summary:
            return False, "holdings_missing_summary"
        if has_risk_tools:
            return False, "holdings_contains_risk_tools"
        return True, "ok"

    if query_type == "market_monitor":
        has_market_tool = any(target == "market_executor" for target in targets)
        if not has_market_tool:
            return False, "market_missing_market_tool"
        return True, "ok"

    return True, "ok"


def _repair_plan_if_needed(
    *,
    steps: list[dict[str, Any]],
    state: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool, bool, str]:
    """Чинит план через fallback, если нарушен минимальный контракт маршрутизации."""
    query_type = str(state.get("query_type", "complex"))
    contract_ok, reason = _plan_contract_status(steps, query_type)
    if contract_ok:
        return steps, True, False, "none"

    fallback_schema = _build_fallback_plan(state)
    fallback_steps = [item.model_dump() for item in fallback_schema.steps]
    repaired = _sanitize_plan_steps(
        steps=fallback_steps,
        user_query=str(state.get("user_query", "")),
        extracted_tickers=list(state.get("extracted_tickers", [])),
        query_type=query_type,
    )
    log.warning("planner_plan_repaired_from_fallback", contract_reason=reason)
    return repaired, False, True, reason


def _build_fallback_plan(state: dict[str, Any]) -> PlanSchema:
    """Формирует детерминированный fallback-план без LLM."""
    query = str(state.get("user_query", ""))
    query_type = str(state.get("query_type", "complex"))
    tickers = list(state.get("extracted_tickers", []))
    first_ticker = tickers[0] if tickers else "SBER"
    portfolio_id = _detect_portfolio_id(query)

    steps: list[PlanStepModel] = []
    step_counter = 1

    if query_type == "portfolio_holdings":
        steps.append(
            PlanStepModel(
                step_number=step_counter,
                description="Получить сводку портфеля (позиции, веса).",
                target_server="analytics_executor",
                tool_name="get_portfolio_summary",
                tool_args={"portfolio_id": portfolio_id},
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
        return PlanSchema(steps=steps, reasoning="План: только состав портфеля (без риск-метрик).")

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

    if query_type == "risk_assessment":
        steps.append(
            PlanStepModel(
                step_number=step_counter,
                description="Получить сводку портфеля (позиции).",
                target_server="analytics_executor",
                tool_name="get_portfolio_summary",
                tool_args={"portfolio_id": portfolio_id},
            )
        )
        step_counter += 1

    if query_type in {"risk_assessment", "complex"}:
        skip_analytics_for_ticker_investment = (
            query_type == "complex"
            and is_investment_decision_intent(query)
            and not _is_stress_intent(query)
            and PORTFOLIO_RE.search(query) is None
        )
        if not skip_analytics_for_ticker_investment:
            if _is_stress_intent(query):
                scenario = "index_drop"
                magnitude = _extract_percent_value(query, 20.0)
                if "ставк" in query.lower():
                    scenario = "rate_hike"
                    magnitude = _extract_percent_value(query, 2.0)
                sector_hint = infer_sector_stress_hint(query)
                if "сектор" in query.lower() or sector_hint:
                    scenario = "sector_decline"
                    magnitude = _extract_percent_value(query, 15.0)
                payload: dict[str, Any] = {
                    "portfolio_id": portfolio_id,
                    "scenario": scenario,
                    "magnitude": magnitude,
                }
                if scenario == "sector_decline" and sector_hint:
                    payload["target_sector"] = sector_hint
                steps.append(
                    PlanStepModel(
                        step_number=step_counter,
                        description="Провести стресс-тестирование портфеля.",
                        target_server="analytics_executor",
                        tool_name="run_stress_test",
                        tool_args=payload,
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


def _estimate_tokens_from_text(text: str) -> int:
    """Грубая оценка числа токенов по длине текста."""
    normalized = text.strip()
    if not normalized:
        return 0
    return max(1, len(normalized) // 4)


def _extract_planner_token_usage(
    response: Any,
    *,
    prompt_input: str,
    output_text: str,
) -> dict[str, Any]:
    """Извлекает usage из ответа провайдера или оценивает токены при отсутствии usage."""
    usage = getattr(response, "usage", None)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    estimated = False

    if usage is not None:
        if isinstance(usage, dict):
            prompt_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
            completion_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
            total_tokens = int(usage.get("total_tokens", 0) or 0)
        else:
            prompt_tokens = int(
                getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", 0)) or 0
            )
            completion_tokens = int(
                getattr(usage, "output_tokens", getattr(usage, "completion_tokens", 0)) or 0
            )
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if total_tokens <= 0:
            total_tokens = prompt_tokens + completion_tokens

    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        prompt_tokens = _estimate_tokens_from_text(prompt_input)
        completion_tokens = _estimate_tokens_from_text(output_text)
        total_tokens = prompt_tokens + completion_tokens
        estimated = True

    return {
        "planner_prompt_tokens": prompt_tokens,
        "planner_completion_tokens": completion_tokens,
        "planner_total_tokens": total_tokens,
        "planner_tokens_estimated": estimated,
    }


def _empty_planner_usage() -> dict[str, Any]:
    """Возвращает usage по умолчанию для случая без LLM-вызова."""
    return {
        "planner_prompt_tokens": 0,
        "planner_completion_tokens": 0,
        "planner_total_tokens": 0,
        "planner_tokens_estimated": False,
    }


def _try_llm_plan(state: dict[str, Any]) -> tuple[PlanSchema | None, dict[str, Any]]:
    """Пытается получить structured plan через LLM."""
    settings = get_settings()
    if not settings.yandex_cloud_api_key or not settings.yandex_cloud_folder:
        log.info("planner_llm_disabled_missing_credentials")
        return None, _empty_planner_usage()

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
    raw_text = response.output_text or ""
    planner_usage = _extract_planner_token_usage(
        response,
        prompt_input=prompt_input,
        output_text=raw_text,
    )
    if not raw_text.strip():
        log.warning("planner_llm_empty_response")
        return None, planner_usage
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.warning("planner_llm_json_invalid", error=str(exc))
        return None, planner_usage
    try:
        plan = PlanSchema.model_validate(payload)
    except ValidationError as exc:
        log.warning("planner_llm_plan_validation_failed", error=str(exc))
        return None, planner_usage
    log.info("planner_llm_plan_received", steps_count=len(plan.steps))
    return plan, planner_usage


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
        planner_usage = _empty_planner_usage()
        llm_plan_used = False
        llm_plan_parse_failed = False
        planner_warnings: list[str] = []
        routing_failure_reason = "none"
        try:
            llm_result = _try_llm_plan(state)
            if isinstance(llm_result, tuple):
                llm_plan, planner_usage = llm_result
            else:
                llm_plan = llm_result
            llm_plan_used = llm_plan is not None
        except Exception:
            llm_plan = None
            llm_plan_parse_failed = True
            planner_warnings.append("План LLM не прошёл валидацию, применён fallback-план.")
            routing_failure_reason = "llm_plan_parse_failed"
            log.warning("planner_llm_plan_failed_fallback")

        schema = llm_plan or _build_fallback_plan(state)
        log.info("planner_plan_source_selected", source="llm" if llm_plan else "fallback")
        normalized_plan = [item.model_dump() for item in schema.steps]
        log.info(
            "planner_plan_raw_steps",
            source="llm" if llm_plan else "fallback",
            steps=_compact_plan_for_log(normalized_plan),
        )
        normalized_plan = _sanitize_plan_steps(
            steps=normalized_plan,
            user_query=str(state.get("user_query", "")),
            extracted_tickers=list(state.get("extracted_tickers", [])),
            query_type=str(state.get("query_type", "complex")),
        )
        normalized_plan, plan_contract_ok, plan_repaired, repair_reason = _repair_plan_if_needed(
            steps=normalized_plan,
            state=state,
        )
        if repair_reason != "none":
            routing_failure_reason = repair_reason
            planner_warnings.append("План скорректирован после проверки контракта маршрутизации.")
        if len(normalized_plan) > 10:
            normalized_plan = normalized_plan[:10]
        next_node = normalized_plan[0]["target_server"] if normalized_plan else "summarizer"
        log.info(
            "planner_plan_built",
            next_node=next_node,
            steps_count=len(normalized_plan),
            steps=_compact_plan_for_log(normalized_plan),
        )
        response: dict[str, Any] = {
            "plan": normalized_plan,
            "current_step": 0,
            "next_node": next_node,
            "llm_plan_used": llm_plan_used,
            "llm_plan_parse_failed": llm_plan_parse_failed,
            "plan_contract_ok": plan_contract_ok,
            "plan_repaired": plan_repaired,
            "routing_failure_reason": routing_failure_reason,
            "planner_prompt_tokens": int(planner_usage.get("planner_prompt_tokens", 0)),
            "planner_completion_tokens": int(planner_usage.get("planner_completion_tokens", 0)),
            "planner_total_tokens": int(planner_usage.get("planner_total_tokens", 0)),
            "planner_tokens_estimated": bool(planner_usage.get("planner_tokens_estimated", False)),
        }
        if planner_warnings:
            merged_warnings = list(state.get("warnings", []))
            merged_warnings.extend(planner_warnings)
            response["warnings"] = merged_warnings
        return response

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

