"""Узел исполнения инструментов analytics_server."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
import structlog

from config.settings import get_settings
from orchestrator.mcp_client import MCPClientError, get_mcp_client
from orchestrator.nodes.planner_node import coerce_stress_magnitude_percent_points

log = structlog.get_logger()
ALLOWED_ANALYTICS_TOOLS = {
    "get_portfolio_summary",
    "calculate_risk_metrics",
    "run_stress_test",
    "execute_analytics_query",
}


def _normalize_analytics_tool_args(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    user_query: str,
) -> tuple[dict[str, Any], str | None]:
    """Нормализует аргументы analytics-инструментов до MCP-контракта."""
    normalized = dict(tool_args)
    portfolio_id = (
        normalized.get("portfolio_id")
        or normalized.get("portfolio_name")
        or normalized.get("name")
        or "demo_portfolio"
    )
    if isinstance(portfolio_id, str):
        portfolio_id = portfolio_id.strip() or "demo_portfolio"
    else:
        portfolio_id = str(portfolio_id)

    warning: str | None = None
    if "portfolio_id" not in normalized:
        warning = (
            "Аргументы analytics шага были нормализованы: добавлен portfolio_id="
            f"{portfolio_id} (query={user_query[:80]})."
        )

    normalized["portfolio_id"] = portfolio_id
    normalized.pop("portfolio_name", None)
    normalized.pop("name", None)
    normalized.pop("tickers", None)

    if tool_name == "get_portfolio_summary":
        return {"portfolio_id": portfolio_id}, warning

    if tool_name == "calculate_risk_metrics":
        confidence = normalized.get("confidence", 0.95)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.95
        if confidence_value <= 0 or confidence_value >= 1:
            confidence_value = 0.95
        return {"portfolio_id": portfolio_id, "confidence": confidence_value}, warning

    if tool_name == "run_stress_test":
        scenario = str(normalized.get("scenario", "index_drop")).strip().lower()
        if scenario in {"market_downturn", "imoex_drop"}:
            scenario = "index_drop"
        if scenario not in {"index_drop", "rate_hike", "sector_decline"}:
            scenario = "index_drop"

        magnitude_raw = normalized.get("magnitude", 20.0 if scenario == "index_drop" else 2.0)
        try:
            magnitude = float(magnitude_raw)
        except (TypeError, ValueError):
            magnitude = 20.0 if scenario == "index_drop" else 2.0
        magnitude = coerce_stress_magnitude_percent_points(scenario, magnitude)

        payload: dict[str, Any] = {
            "portfolio_id": portfolio_id,
            "scenario": scenario,
            "magnitude": magnitude,
        }
        target_sector = normalized.get("target_sector")
        if scenario == "sector_decline" and isinstance(target_sector, str) and target_sector.strip():
            payload["target_sector"] = target_sector.strip()
        return payload, warning

    return normalized, warning


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
    log.info("analytics_executor_step_started", current_step=current_step, tool_name=tool_name, tool_args=tool_args)
    if tool_name not in ALLOWED_ANALYTICS_TOOLS:
        return {
            "error_count": int(state.get("error_count", 0)) + 1,
            "current_step": current_step + 1,
            "warnings": [f"Отклонен неразрешенный analytics tool: {tool_name}"],
            "messages": [AIMessage(content=f"Отклонен неразрешенный analytics tool: {tool_name}")],
        }

    client = get_mcp_client()
    settings = get_settings()
    normalized_tool_args, normalization_warning = _normalize_analytics_tool_args(
        tool_name=tool_name,
        tool_args=tool_args,
        user_query=str(state.get("user_query", "")),
    )
    warnings = list(state.get("warnings", []))
    if normalization_warning:
        warnings.append(normalization_warning)
        log.info(
            "analytics_executor_args_normalized",
            tool_name=tool_name,
            normalized_tool_args=normalized_tool_args,
            warning=normalization_warning,
        )

    try:
        result = await client.call_tool(tool_name, normalized_tool_args)
        metrics = dict(state.get("portfolio_metrics", {}))
        metrics[tool_name] = result
        response: dict[str, Any] = {
            "portfolio_metrics": metrics,
            "current_step": current_step + 1,
            "messages": [AIMessage(content=f"Analytics step выполнен: {tool_name}")],
        }
        if warnings:
            response["warnings"] = warnings
        if tool_name == "calculate_risk_metrics" and not result:
            response["warnings"] = [
                *warnings,
                "Не удалось получить риск-метрики в полном объеме.",
            ]
        log.info(
            "analytics_executor_step_succeeded",
            tool_name=tool_name,
            warnings_count=len(response.get("warnings", [])),
        )
        return response
    except MCPClientError as error:
        metrics = dict(state.get("portfolio_metrics", {}))
        log.warning("analytics_executor_step_failed", tool_name=tool_name, error=str(error))
        if tool_name == "calculate_risk_metrics":
            try:
                fallback_payload = await client.call_tool(
                    "get_portfolio_summary",
                    {"portfolio_id": normalized_tool_args.get("portfolio_id", "demo_portfolio")},
                )
                metrics["fallback_portfolio_summary"] = fallback_payload
                warnings.append(
                    "Расчет риск-метрик завершился ошибкой, показана упрощенная сводка портфеля."
                )
                return {
                    "portfolio_metrics": metrics,
                    "current_step": current_step + 1,
                    "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
                    "warnings": warnings,
                    "messages": [AIMessage(content=f"Fallback analytics path used: {error}")],
                }
            except Exception:
                log.warning("analytics_executor_fallback_failed", tool_name=tool_name)
                pass

        warnings.append("Часть аналитических данных недоступна, точность оценки риска снижена.")
        return {
            "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
            "current_step": current_step + 1,
            "warnings": warnings,
            "messages": [AIMessage(content=f"Ошибка analytics_executor: {error}")],
        }
    except Exception as error:
        metrics = dict(state.get("portfolio_metrics", {}))
        log.warning("analytics_executor_step_failed", tool_name=tool_name, error=str(error))
        if tool_name == "calculate_risk_metrics":
            try:
                fallback_payload = await client.call_tool(
                    "get_portfolio_summary",
                    {"portfolio_id": normalized_tool_args.get("portfolio_id", "demo_portfolio")},
                )
                metrics["fallback_portfolio_summary"] = fallback_payload
                warnings.append(
                    "Расчет риск-метрик завершился ошибкой, показана упрощенная сводка портфеля."
                )
                return {
                    "portfolio_metrics": metrics,
                    "current_step": current_step + 1,
                    "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
                    "warnings": warnings,
                    "messages": [AIMessage(content=f"Fallback analytics path used: {error}")],
                }
            except Exception:
                log.warning("analytics_executor_fallback_failed", tool_name=tool_name)
                pass
        warnings.append("Часть аналитических данных недоступна, точность оценки риска снижена.")
        return {
            "error_count": min(int(state.get("error_count", 0)) + 1, settings.max_error_count),
            "current_step": current_step + 1,
            "warnings": warnings,
            "messages": [AIMessage(content=f"Ошибка analytics_executor: {error}")],
        }

