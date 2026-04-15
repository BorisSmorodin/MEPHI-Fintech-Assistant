"""Сборка графа выполнения LangGraph."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from config.settings import get_settings
from orchestrator.nodes.analytics_executor import analytics_executor
from orchestrator.nodes.input_node import input_node
from orchestrator.nodes.market_executor import market_executor
from orchestrator.nodes.news_executor import news_executor
from orchestrator.nodes.planner_node import planner_node, route_planner
from orchestrator.nodes.summarizer_node import summarizer_node
from orchestrator.state import InvestmentAssistantState, initial_state


def build_graph():
    """Собирает граф оркестратора."""
    builder = StateGraph(InvestmentAssistantState)
    builder.add_node("input", input_node)
    builder.add_node("planner", planner_node)
    builder.add_node("market_executor", market_executor)
    builder.add_node("news_executor", news_executor)
    builder.add_node("analytics_executor", analytics_executor)
    builder.add_node("summarizer", summarizer_node)

    builder.add_edge(START, "input")
    builder.add_edge("input", "planner")
    builder.add_conditional_edges(
        "planner",
        route_planner,
        {
            "market_executor": "market_executor",
            "news_executor": "news_executor",
            "analytics_executor": "analytics_executor",
            "summarizer": "summarizer",
        },
    )
    builder.add_edge("market_executor", "planner")
    builder.add_edge("news_executor", "planner")
    builder.add_edge("analytics_executor", "planner")
    builder.add_edge("summarizer", END)
    return builder.compile(checkpointer=MemorySaver())


graph = build_graph()


async def run_query(user_query: str, state_overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Запускает граф по пользовательскому запросу и возвращает итоговый state."""
    settings = get_settings()
    payload = initial_state(user_query=user_query)
    if state_overrides:
        payload.update(state_overrides)
    result = await graph.ainvoke(
        payload,
        config={
            "recursion_limit": settings.max_recursion,
            "configurable": {"thread_id": f"thread-{uuid4()}"},
        },
    )
    return dict(result)

