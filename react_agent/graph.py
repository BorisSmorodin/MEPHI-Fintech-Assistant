"""Сборка ReAct-агента и запуск запроса."""

from __future__ import annotations

from collections.abc import Sequence
import time
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
import structlog

from config.settings import get_settings
from react_agent.llm import build_chat_model
from react_agent.mcp_tools import load_mcp_tools
from react_agent.prompts import REACT_SYSTEM_PROMPT
from react_agent.quality_metrics import collect_react_quality_metrics
from react_agent.trace import count_tool_messages, extract_final_answer, extract_tool_trace

log = structlog.get_logger()


def build_react_graph(
    tools: Sequence[BaseTool],
    *,
    model: BaseChatModel | None = None,
) -> CompiledStateGraph:
    """Собирает скомпилированный граф ReAct на заданных инструментах."""
    chat = model or build_chat_model()
    return create_react_agent(chat, list(tools), prompt=REACT_SYSTEM_PROMPT)


async def run_react_query(
    user_query: str,
    *,
    scenario_name: str = "adhoc",
    expected_servers: set[str] | None = None,
    recursion_limit: int | None = None,
    tools: list[BaseTool] | None = None,
    model: BaseChatModel | None = None,
) -> dict[str, Any]:
    """
    Запускает ReAct-агент с MCP-инструментами и возвращает сообщения, трассировку и ответ.

    Args:
        user_query: Текст пользователя.
        recursion_limit: Лимит шагов LangGraph; по умолчанию из settings.max_recursion.
        tools: Список инструментов; если None — загружаются из MCP.
        model: Chat-модель; если None — build_chat_model() из конфига.
    """
    settings = get_settings()
    limit = recursion_limit if recursion_limit is not None else settings.max_recursion
    tool_list = tools if tools is not None else await load_mcp_tools()
    graph = build_react_graph(tool_list, model=model)

    log.info(
        "react_agent_run_started",
        query_preview=user_query[:200],
        tools_count=len(tool_list),
        recursion_limit=limit,
        scenario_name=scenario_name,
    )
    started_at = time.perf_counter()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=user_query)]},
        config={"recursion_limit": limit},
    )
    elapsed_sec = time.perf_counter() - started_at
    messages = list(result.get("messages", []))
    tool_trace = extract_tool_trace(messages)
    final_answer = extract_final_answer(messages)
    mcp_calls = count_tool_messages(messages)
    quality_metrics = collect_react_quality_metrics(
        scenario_name=scenario_name,
        user_query=user_query,
        expected_servers=expected_servers,
        response_time_sec=elapsed_sec,
        tool_trace=tool_trace,
        final_answer=final_answer,
        mcp_calls_count=mcp_calls,
        messages=messages,
    )

    log.info(
        "react_agent_run_completed",
        messages_count=len(messages),
        mcp_calls_count=mcp_calls,
        tool_trace_steps=len(tool_trace),
        has_final_answer=bool(final_answer),
        elapsed_sec=round(elapsed_sec, 6),
        scenario_name=scenario_name,
    )
    return {
        "messages": messages,
        "tool_trace": tool_trace,
        "final_answer": final_answer,
        "mcp_calls_count": mcp_calls,
        "error_count": int(quality_metrics.get("error_count_final", 0)),
        "used_servers": list(quality_metrics.get("used_servers", [])),
        "tool_selection_correct": bool(quality_metrics.get("tool_selection_correct", False)),
        "tokens_prompt": int(quality_metrics.get("tokens_prompt", 0)),
        "tokens_completion": int(quality_metrics.get("tokens_completion", 0)),
        "tokens_total": int(quality_metrics.get("tokens_total", 0)),
        "tokens_estimated": bool(quality_metrics.get("tokens_estimated", False)),
        "quality_metrics": quality_metrics,
    }
