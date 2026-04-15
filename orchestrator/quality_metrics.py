"""Сбор и агрегация метрик качества сценариев."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any

import structlog

log = structlog.get_logger()


def _normalize_expected_servers(expected_servers: set[str] | None, state: dict[str, Any]) -> set[str]:
    """Возвращает ожидаемый набор серверов для сценария."""
    if expected_servers:
        return set(expected_servers)

    query_type = str(state.get("query_type", "complex"))
    if query_type == "market_monitor":
        return {"market"}
    if query_type == "news_analysis":
        return {"news"}
    if query_type == "risk_assessment":
        return {"analytics"}
    return {"market", "news", "analytics"}


def _observed_servers_from_plan(state: dict[str, Any]) -> set[str]:
    """Оценивает фактически задействованные серверы по выполненным шагам плана."""
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    observed_servers: set[str] = set()
    for index, step in enumerate(plan):
        if index >= current_step:
            break
        target_server = str(step.get("target_server", ""))
        if target_server == "market_executor":
            observed_servers.add("market")
        elif target_server == "news_executor":
            observed_servers.add("news")
        elif target_server == "analytics_executor":
            observed_servers.add("analytics")
    return observed_servers


def _estimate_mcp_calls_count(state: dict[str, Any]) -> int:
    """Оценивает число MCP-вызовов по текущему плану и шагам."""
    plan = list(state.get("plan", []))
    current_step = int(state.get("current_step", 0))
    call_count = 0
    for index, step in enumerate(plan):
        if index >= current_step:
            break
        if str(step.get("target_server", "")) in {"market_executor", "news_executor", "analytics_executor"}:
            call_count += 1
    return call_count


def collect_quality_metrics(
    *,
    state: dict[str, Any],
    user_query: str,
    scenario_name: str,
    response_time_sec: float,
    expected_servers: set[str] | None = None,
) -> dict[str, Any]:
    """Формирует словарь метрик качества по результатам сценария."""
    normalized_expected = _normalize_expected_servers(expected_servers, state)
    observed_servers = _observed_servers_from_plan(state)
    scenario_success = bool(str(state.get("final_answer") or "").strip())
    tool_selection_correct = normalized_expected.issubset(observed_servers)

    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "scenario_name": scenario_name,
        "user_query": user_query,
        "scenario_success": scenario_success,
        "tool_selection_correct": tool_selection_correct,
        "response_time_sec": round(response_time_sec, 6),
        "mcp_calls_count": _estimate_mcp_calls_count(state),
        "error_count_final": int(state.get("error_count", 0)),
        "expected_servers": sorted(normalized_expected),
        "observed_servers": sorted(observed_servers),
    }
    log.info("quality_metrics_collected", **record)
    return record


def append_quality_metric(record: dict[str, Any], metrics_path: str) -> Path:
    """Добавляет метрику в JSONL-файл."""
    path = Path(metrics_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False))
        file.write("\n")
    return path


def build_quality_metrics_report(metrics_path: str) -> dict[str, Any]:
    """Строит агрегированный отчет по JSONL-файлу метрик."""
    path = Path(metrics_path)
    if not path.exists():
        return {
            "metrics_path": str(path),
            "records_count": 0,
            "scenario_success_rate": 0.0,
            "tool_selection_correct_rate": 0.0,
            "avg_response_time_sec": 0.0,
            "avg_mcp_calls_count": 0.0,
            "avg_error_count_final": 0.0,
        }

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    if not records:
        return {
            "metrics_path": str(path),
            "records_count": 0,
            "scenario_success_rate": 0.0,
            "tool_selection_correct_rate": 0.0,
            "avg_response_time_sec": 0.0,
            "avg_mcp_calls_count": 0.0,
            "avg_error_count_final": 0.0,
        }

    scenario_success_rate = mean(1.0 if row.get("scenario_success") else 0.0 for row in records)
    tool_selection_correct_rate = mean(1.0 if row.get("tool_selection_correct") else 0.0 for row in records)
    avg_response_time = mean(float(row.get("response_time_sec", 0.0)) for row in records)
    avg_mcp_calls = mean(int(row.get("mcp_calls_count", 0)) for row in records)
    avg_errors = mean(int(row.get("error_count_final", 0)) for row in records)

    return {
        "metrics_path": str(path),
        "records_count": len(records),
        "scenario_success_rate": round(scenario_success_rate, 6),
        "tool_selection_correct_rate": round(tool_selection_correct_rate, 6),
        "avg_response_time_sec": round(avg_response_time, 6),
        "avg_mcp_calls_count": round(avg_mcp_calls, 6),
        "avg_error_count_final": round(avg_errors, 6),
    }
