"""Описание состояния графа инвестиционного ассистента."""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

QueryType = Literal["market_monitor", "news_analysis", "risk_assessment", "complex"]
ExecutorTarget = Literal["market_executor", "news_executor", "analytics_executor", "summarizer"]


class PlanStep(TypedDict):
    """Шаг плана выполнения в оркестраторе."""

    step_number: int
    description: str
    target_server: ExecutorTarget
    tool_name: str
    tool_args: dict[str, Any]


class InvestmentAssistantState(TypedDict):
    """Состояние графа инвестиционного ассистента."""

    messages: Annotated[list[Any], add_messages]
    user_query: str
    query_type: QueryType
    plan: list[PlanStep]
    current_step: int
    market_data: dict[str, Any]
    news_data: list[dict[str, Any]]
    portfolio_metrics: dict[str, Any]
    final_answer: str | None
    error_count: int
    next_node: ExecutorTarget | None
    extracted_tickers: list[str]
    warnings: list[str]
    investment_decision_intent: bool


def initial_state(user_query: str = "") -> InvestmentAssistantState:
    """Возвращает начальное состояние для запуска графа."""
    return {
        "messages": [],
        "user_query": user_query,
        "query_type": "complex",
        "plan": [],
        "current_step": 0,
        "market_data": {},
        "news_data": [],
        "portfolio_metrics": {},
        "final_answer": None,
        "error_count": 0,
        "next_node": None,
        "extracted_tickers": [],
        "warnings": [],
        "investment_decision_intent": False,
    }

