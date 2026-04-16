"""Узел исполнения инструментов news_server."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
import structlog

from config.settings import get_settings
from orchestrator.mcp_client import MCPClientError, get_mcp_client

log = structlog.get_logger()
ALLOWED_NEWS_TOOLS = {
    "fetch_news",
    "get_cb_key_rate",
    "get_market_sentiment",
    "get_macro_calendar",
}


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
    log.info("news_executor_step_started", current_step=current_step, tool_name=tool_name, tool_args=tool_args)
    settings = get_settings()
    if tool_name not in ALLOWED_NEWS_TOOLS:
        return {
            "error_count": int(state.get("error_count", 0)) + 1,
            "current_step": current_step + 1,
            "warnings": [f"Отклонен неразрешенный news tool: {tool_name}"],
            "messages": [AIMessage(content=f"Отклонен неразрешенный news tool: {tool_name}")],
        }

    client = get_mcp_client()

    try:
        result = await client.call_tool(tool_name, tool_args)
        news_data = list(state.get("news_data", []))
        warnings = list(state.get("warnings", []))
        if isinstance(result, list):
            news_data.extend(result)
            if tool_name == "fetch_news" and not result:
                warnings.append("По запросу не найдено новостей в доступных источниках.")
        else:
            news_data.append({"tool": tool_name, "payload": result})
        response: dict[str, Any] = {
            "news_data": news_data,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"News step выполнен: {tool_name}")],
        }
        if warnings:
            response["warnings"] = warnings
        log.info(
            "news_executor_step_succeeded",
            tool_name=tool_name,
            added_rows=len(result) if isinstance(result, list) else 1,
            warnings_count=len(response.get("warnings", [])),
        )
        return response
    except MCPClientError as error:
        new_error_count = int(state.get("error_count", 0)) + 1
        warnings = list(state.get("warnings", []))
        warnings.append("Часть новостных источников недоступна, охват новостей ограничен.")
        log.warning("news_executor_step_failed", tool_name=tool_name, error=str(error), error_count=new_error_count)
        if new_error_count >= settings.max_error_count:
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
        warnings.append("Часть новостных источников недоступна, охват новостей ограничен.")
        log.warning("news_executor_step_failed", tool_name=tool_name, error=str(error), error_count=new_error_count)
        if new_error_count >= settings.max_error_count:
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

