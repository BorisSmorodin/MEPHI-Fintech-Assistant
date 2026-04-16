"""Метрики качества для ReAct-агента."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

TOOL_TO_SERVER = {
    "get_stock_quote": "market",
    "get_candles": "market",
    "get_board_securities": "market",
    "get_index_analytics": "market",
    "get_bond_data": "market",
    "fetch_news": "news",
    "get_cb_key_rate": "news",
    "get_market_sentiment": "news",
    "get_macro_calendar": "news",
    "get_portfolio_summary": "analytics",
    "calculate_risk_metrics": "analytics",
    "run_stress_test": "analytics",
    "execute_analytics_query": "analytics",
}


def estimate_tokens_from_text(text: str) -> int:
    """Грубая оценка числа токенов по длине текста."""
    normalized = text.strip()
    if not normalized:
        return 0
    return max(1, len(normalized) // 4)


def used_servers_from_tool_trace(tool_trace: list[dict[str, Any]]) -> set[str]:
    """Возвращает фактически задействованные серверы по трассировке вызовов."""
    used: set[str] = set()
    for item in tool_trace:
        tool_name = str(item.get("tool_name", ""))
        mapped = TOOL_TO_SERVER.get(tool_name)
        if mapped:
            used.add(mapped)
    return used


def estimate_react_error_count(tool_trace: list[dict[str, Any]], final_answer: str | None) -> int:
    """Оценивает количество ошибок в прогоне по эвристике текстов tool results/ответа."""
    errors = 0
    for item in tool_trace:
        preview = str(item.get("result_preview") or "").casefold()
        if any(marker in preview for marker in ("ошиб", "error", "traceback", "исключен")):
            errors += 1
    if not (final_answer or "").strip():
        errors += 1
    return errors


def extract_react_token_usage(
    messages: Sequence[BaseMessage],
    *,
    user_query: str,
    final_answer: str | None,
) -> dict[str, Any]:
    """Извлекает usage из AI-сообщений или оценивает токены при отсутствии usage."""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    estimated = False

    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        usage = getattr(message, "usage_metadata", None) or {}
        if not isinstance(usage, dict):
            continue
        prompt_tokens += int(usage.get("input_tokens", 0) or 0)
        completion_tokens += int(usage.get("output_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens <= 0 and completion_tokens <= 0 and total_tokens <= 0:
        prompt_tokens = estimate_tokens_from_text(user_query)
        completion_tokens = estimate_tokens_from_text(final_answer or "")
        total_tokens = prompt_tokens + completion_tokens
        estimated = True

    return {
        "tokens_prompt": prompt_tokens,
        "tokens_completion": completion_tokens,
        "tokens_total": total_tokens,
        "tokens_estimated": estimated,
    }


def collect_react_quality_metrics(
    *,
    scenario_name: str,
    user_query: str,
    expected_servers: set[str] | None,
    response_time_sec: float,
    tool_trace: list[dict[str, Any]],
    final_answer: str | None,
    mcp_calls_count: int,
    messages: Sequence[BaseMessage],
) -> dict[str, Any]:
    """Формирует quality-record для ReAct в формате, близком к оркестратору."""
    used_servers = used_servers_from_tool_trace(tool_trace)
    expected = expected_servers or set()
    token_usage = extract_react_token_usage(
        messages,
        user_query=user_query,
        final_answer=final_answer,
    )
    errors = estimate_react_error_count(tool_trace, final_answer)
    return {
        "scenario_name": scenario_name,
        "user_query": user_query,
        "scenario_success": bool((final_answer or "").strip()),
        "response_time_sec": round(response_time_sec, 6),
        "mcp_calls_count": int(mcp_calls_count),
        "error_count_final": errors,
        "expected_servers": sorted(expected),
        "used_servers": sorted(used_servers),
        "tool_selection_correct": expected.issubset(used_servers) if expected else True,
        **token_usage,
    }
