"""Узел исполнения инструментов analytics_server."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from config.settings import get_settings
from orchestrator.mcp_client import MCPClientError, get_mcp_client

ALLOWED_ANALYTICS_TOOLS = {
    "get_portfolio_summary",
    "calculate_risk_metrics",
    "run_stress_test",
    "execute_analytics_query",
}


async def analytics_executor(state: dict[str, Any]) -> dict[str, Any]:
    """Выполняет шаги плана, адресованные analytics_server."""
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    if current_step >= len(plan):
        return {}

    step = plan[current_step]
    if step.get("target_server") != "analytics_executor":
        return {"current_step": current_step + 1}

    tool_name = str(step.get("tool_name", ""))
    tool_args = dict(step.get("tool_args", {}))
    if tool_name not in ALLOWED_ANALYTICS_TOOLS:
        return {
            "error_count": int(state.get("error_count", 0)) + 1,
            "current_step": current_step + 1,
            "warnings": [f"Отклонен неразрешенный analytics tool: {tool_name}"],
            "messages": [AIMessage(content=f"Отклонен неразрешенный analytics tool: {tool_name}")],
        }

    client = get_mcp_client()
    settings = get_settings()

    try:
        result = await client.call_tool(tool_name, tool_args)
        metrics = dict(state.get("portfolio_metrics", {}))
        metrics[tool_name] = result
        return {
            "portfolio_metrics": metrics,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Analytics step выполнен: {tool_name}")],
        }
    except MCPClientError as error:
        metrics = dict(state.get("portfolio_metrics", {}))
        if tool_name == "calculate_risk_metrics":
            try:
                fallback_payload = await client.call_tool(
                    "get_portfolio_summary",
                    {"portfolio_id": tool_args.get("portfolio_id", "demo_portfolio")},
                )
                metrics["fallback_portfolio_summary"] = fallback_payload
                return {
                    "portfolio_metrics": metrics,
                    "current_step": current_step + 1,
                    "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
                    "messages": [AIMessage(content=f"Fallback analytics path used: {error}")],
                }
            except Exception:
                pass

        return {
            "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Ошибка analytics_executor: {error}")],
        }
    except Exception as error:
        metrics = dict(state.get("portfolio_metrics", {}))
        if tool_name == "calculate_risk_metrics":
            try:
                fallback_payload = await client.call_tool(
                    "get_portfolio_summary",
                    {"portfolio_id": tool_args.get("portfolio_id", "demo_portfolio")},
                )
                metrics["fallback_portfolio_summary"] = fallback_payload
                return {
                    "portfolio_metrics": metrics,
                    "current_step": current_step + 1,
                    "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
                    "messages": [AIMessage(content=f"Fallback analytics path used: {error}")],
                }
            except Exception:
                pass
        return {
            "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Ошибка analytics_executor: {error}")],
        }

