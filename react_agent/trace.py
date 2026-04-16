"""Разбор сообщений LangGraph/LangChain для сравнения вызовов инструментов."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def _normalize_tool_args(raw: Any) -> Any:
    """Приводит аргументы tool call к dict/объекту для логов и сравнения."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return raw


def _iter_tool_calls(ai: AIMessage) -> list[dict[str, Any]]:
    """Извлекает tool_calls из AIMessage в единообразном виде."""
    raw = getattr(ai, "tool_calls", None) or []
    out: list[dict[str, Any]] = []
    for tc in raw:
        if isinstance(tc, dict):
            name = tc.get("name", "")
            tid = tc.get("id")
            args = tc.get("args")
            if args is None and "function" in tc:
                fn = tc.get("function") or {}
                name = name or fn.get("name", "")
                args = fn.get("arguments")
        else:
            name = getattr(tc, "name", "") or ""
            tid = getattr(tc, "id", None)
            args = getattr(tc, "args", None)
        out.append({"name": str(name), "id": tid, "args": _normalize_tool_args(args)})
    return out


def extract_tool_trace(messages: Sequence[BaseMessage]) -> list[dict[str, Any]]:
    """Строит упорядоченную трассировку вызовов инструментов и ответов tool-узла."""
    trace: list[dict[str, Any]] = []
    pending_by_id: dict[str, int] = {}

    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in _iter_tool_calls(msg):
                entry = {
                    "tool_name": tc["name"],
                    "tool_args": tc["args"],
                    "tool_call_id": tc["id"],
                    "result_preview": None,
                }
                trace.append(entry)
                if tc["id"] is not None:
                    pending_by_id[str(tc["id"])] = len(trace) - 1
        elif isinstance(msg, ToolMessage):
            tid = getattr(msg, "tool_call_id", None)
            preview: str
            if isinstance(msg.content, str):
                preview = msg.content[:500]
            else:
                preview = str(msg.content)[:500]
            if tid is not None and str(tid) in pending_by_id:
                idx = pending_by_id[str(tid)]
                trace[idx]["result_preview"] = preview
            else:
                trace.append(
                    {
                        "tool_name": "_unmatched_tool_message",
                        "tool_args": {},
                        "tool_call_id": tid,
                        "result_preview": preview,
                    }
                )

    return trace


def extract_final_answer(messages: Sequence[BaseMessage]) -> str | None:
    """Возвращает текст финального ответа ассистента (последнее непустое AI-сообщение)."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list) and content:
                return str(content).strip()
    return None


def count_tool_messages(messages: Sequence[BaseMessage]) -> int:
    """Число сообщений с результатами инструментов (обычно совпадает с числом MCP-вызовов)."""
    return sum(1 for m in messages if isinstance(m, ToolMessage))
