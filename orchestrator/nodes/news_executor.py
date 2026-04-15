"""Узел исполнения инструментов news_server."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from orchestrator.mcp_client import MCPClientError, get_mcp_client


async def news_executor(state: dict[str, Any]) -> dict[str, Any]:
    """Выполняет шаги плана, адресованные news_server."""
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    if current_step >= len(plan):
        return {}

    step = plan[current_step]
    if step.get("target_server") != "news_executor":
        return {"current_step": current_step + 1}

    tool_name = str(step.get("tool_name", ""))
    tool_args = dict(step.get("tool_args", {}))
    client = get_mcp_client()

    try:
        result = await client.call_tool(tool_name, tool_args)
        news_data = list(state.get("news_data", []))
        if isinstance(result, list):
            news_data.extend(result)
        else:
            news_data.append({"tool": tool_name, "payload": result})
        return {
            "news_data": news_data,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"News step выполнен: {tool_name}")],
        }
    except MCPClientError as error:
        new_error_count = int(state.get("error_count", 0)) + 1
        warnings = list(state.get("warnings", []))
        if new_error_count >= 3:
            warnings.append("news_server временно недоступен, продолжаем без части новостей.")
            return {
                "warnings": warnings,
                "error_count": new_error_count,
                "current_step": current_step + 1,
                "messages": [AIMessage(content=f"News degraded mode: {error}")],
            }
        return {
            "error_count": new_error_count,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Ошибка news_executor: {error}")],
        }
    except Exception as error:
        new_error_count = int(state.get("error_count", 0)) + 1
        warnings = list(state.get("warnings", []))
        if new_error_count >= 3:
            warnings.append("news_server временно недоступен, продолжаем без части новостей.")
            return {
                "warnings": warnings,
                "error_count": new_error_count,
                "current_step": current_step + 1,
                "messages": [AIMessage(content=f"News degraded mode: {error}")],
            }
        return {
            "error_count": new_error_count,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Ошибка news_executor: {error}")],
        }

