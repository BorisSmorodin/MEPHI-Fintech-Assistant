"""Узел планирования последовательности вызовов инструментов."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from openai import OpenAI
from pydantic import BaseModel, Field

from config.settings import get_settings
from orchestrator.prompts import PLANNER_SYSTEM_PROMPT


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
        return None
    payload = json.loads(raw_text)
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
    if int(state.get("error_count", 0)) >= settings.max_error_count:
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

        schema = llm_plan or _build_fallback_plan(state)
        normalized_plan = [item.model_dump() for item in schema.steps]
        if len(normalized_plan) > 10:
            normalized_plan = normalized_plan[:10]
        next_node = normalized_plan[0]["target_server"] if normalized_plan else "summarizer"
        return {
            "plan": normalized_plan,
            "current_step": 0,
            "next_node": next_node,
        }

    if current_step >= len(plan) or current_step >= 10:
        return {"next_node": "summarizer"}

    step = plan[current_step]
    target = step.get("target_server", "summarizer")
    if target not in {"market_executor", "news_executor", "analytics_executor", "summarizer"}:
        target = "summarizer"
    return {"next_node": target}

