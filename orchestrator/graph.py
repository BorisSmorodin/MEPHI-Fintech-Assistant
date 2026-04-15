"""Сборка графа выполнения LangGraph."""

from __future__ import annotations

import time
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
from orchestrator.quality_metrics import (
    append_quality_metric,
    collect_quality_metrics,
)
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


async def run_query(
    user_query: str,
    state_overrides: dict[str, Any] | None = None,
    *,
    scenario_name: str = "adhoc",
    expected_servers: set[str] | None = None,
    persist_metrics: bool | None = None,
    metrics_path: str | None = None,
) -> dict[str, Any]:
    """Запускает граф по пользовательскому запросу и возвращает итоговый state."""
    settings = get_settings()
    payload = initial_state(user_query=user_query)
    if state_overrides:
        payload.update(state_overrides)
    started_at = time.perf_counter()
    result = await graph.ainvoke(
        payload,
        config={
            "recursion_limit": settings.max_recursion,
            "configurable": {"thread_id": f"thread-{uuid4()}"},
        },
    )
    output_state = dict(result)
    elapsed_sec = time.perf_counter() - started_at
    quality_metrics = collect_quality_metrics(
        state=output_state,
        user_query=user_query,
        scenario_name=scenario_name,
        response_time_sec=elapsed_sec,
        expected_servers=expected_servers,
    )
    output_state["quality_metrics"] = quality_metrics

    should_persist = settings.quality_metrics_enable_file if persist_metrics is None else persist_metrics
    if should_persist:
        destination = metrics_path or settings.quality_metrics_path
        append_quality_metric(quality_metrics, destination)
    return output_state

