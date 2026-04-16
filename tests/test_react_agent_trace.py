"""Тесты трассировки ReAct и сборки графа без MCP."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from react_agent.graph import build_react_graph
from react_agent.trace import (
    count_tool_messages,
    extract_final_answer,
    extract_tool_trace,
)


def test_extract_tool_trace_pairs_calls_and_results() -> None:
    ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_stock_quote",
                "id": "call_1",
                "args": {"ticker": "SBER"},
            }
        ],
    )
    tool_msg = ToolMessage(content='{"price": 300}', tool_call_id="call_1")
    final = AIMessage(content="Котировка получена.")
    trace = extract_tool_trace([HumanMessage(content="hi"), ai, tool_msg, final])
    assert len(trace) == 1
    assert trace[0]["tool_name"] == "get_stock_quote"
    assert trace[0]["tool_args"] == {"ticker": "SBER"}
    assert trace[0]["result_preview"] is not None
    assert "300" in trace[0]["result_preview"]


def test_extract_final_answer_prefers_last_non_empty_ai() -> None:
    messages = [
        AIMessage(content="", tool_calls=[]),
        AIMessage(content="Итоговый текст."),
    ]
    assert extract_final_answer(messages) == "Итоговый текст."


def test_count_tool_messages() -> None:
    msgs = [ToolMessage(content="a", tool_call_id="1"), HumanMessage(content="x")]
    assert count_tool_messages(msgs) == 1


def test_build_react_graph_compiles_without_mcp() -> None:
    model = ChatOpenAI(
        api_key="sk-test-not-used-at-compile",
        base_url="http://127.0.0.1:9",
        model="gpt-test",
    )
    graph = build_react_graph([], model=model)
    assert graph is not None
