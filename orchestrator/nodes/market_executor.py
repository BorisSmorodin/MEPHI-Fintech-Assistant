"""Узел исполнения инструментов market_server."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import AIMessage

from config.settings import get_settings
from orchestrator.mcp_client import MCPClientError, get_mcp_client

ALLOWED_MARKET_TOOLS = {
    "get_stock_quote",
    "get_candles",
    "get_board_securities",
    "get_index_analytics",
    "get_bond_data",
}


async def market_executor(state: dict[str, Any]) -> dict[str, Any]:
    """Выполняет шаги плана, адресованные market_server."""
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    if current_step >= len(plan):
        return {}

    step = plan[current_step]
    if step.get("target_server") != "market_executor":
        return {"current_step": current_step + 1}

    tool_name = str(step.get("tool_name", ""))
    tool_args = dict(step.get("tool_args", {}))
    settings = get_settings()
    if tool_name not in ALLOWED_MARKET_TOOLS:
        return {
            "error_count": int(state.get("error_count", 0)) + 1,
            "current_step": current_step + 1,
            "warnings": [f"Отклонен неразрешенный market tool: {tool_name}"],
            "messages": [AIMessage(content=f"Отклонен неразрешенный market tool: {tool_name}")],
        }

    client = get_mcp_client()
    delays = [1.0, 2.0, 4.0]
    last_error = ""

    for attempt in range(3):
        try:
            result = await client.call_tool(tool_name, tool_args)
            market_data = dict(state.get("market_data", {}))
            market_data[tool_name] = result
            return {
                "market_data": market_data,
                "current_step": current_step + 1,
                "messages": [AIMessage(content=f"Market step выполнен: {tool_name}")],
            }
        except MCPClientError as error:
            last_error = str(error)
            if "не найден" in last_error.lower():
                break
            if attempt < len(delays) - 1:
                await asyncio.sleep(delays[attempt])
        except Exception as error:
            last_error = str(error)
            if "не найден" in last_error.lower():
                break
            if attempt < len(delays) - 1:
                await asyncio.sleep(delays[attempt])

    return {
        "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
        "current_step": current_step + 1,
        "messages": [AIMessage(content=f"Ошибка market_executor: {last_error}")],
    }

