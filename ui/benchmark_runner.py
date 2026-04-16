"""Массовый прогон benchmark-матрицы для оркестратора и ReAct."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from statistics import mean, median
import time
from typing import Any

import structlog

from config.settings import get_settings
from orchestrator.graph import run_query
from react_agent.graph import run_react_query
from ui.benchmark_catalog import BenchmarkScenario

log = structlog.get_logger()


def _append_jsonl_record(path: Path, record: dict[str, Any]) -> None:
    """Добавляет запись в JSONL benchmark-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False))
        fp.write("\n")


@contextmanager
def override_yandex_model(model_name: str):
    """Временно переопределяет модель через YANDEX_CLOUD_MODEL."""
    previous = os.environ.get("YANDEX_CLOUD_MODEL")
    os.environ["YANDEX_CLOUD_MODEL"] = model_name
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("YANDEX_CLOUD_MODEL", None)
        else:
            os.environ["YANDEX_CLOUD_MODEL"] = previous
        get_settings.cache_clear()


def _normalize_orchestrator_record(
    *,
    model_name: str,
    scenario: BenchmarkScenario,
    quality_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Приводит запись оркестратора к общему формату benchmark."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "system": "orchestrator",
        "model": model_name,
        "scenario_name": scenario.name,
        "user_query": scenario.query,
        "latency_sec": float(quality_metrics.get("response_time_sec", 0.0)),
        "scenario_success": bool(quality_metrics.get("scenario_success", False)),
        "error_count": int(quality_metrics.get("error_count_final", 0)),
        "expected_servers": list(quality_metrics.get("expected_servers", [])),
        "used_servers": list(quality_metrics.get("observed_servers", [])),
        "tool_selection_correct": bool(quality_metrics.get("tool_selection_correct", False)),
        "mcp_calls_count": int(quality_metrics.get("mcp_calls_count", 0)),
        "tokens_prompt": int(quality_metrics.get("tokens_prompt", 0)),
        "tokens_completion": int(quality_metrics.get("tokens_completion", 0)),
        "tokens_total": int(quality_metrics.get("tokens_total", 0)),
        "tokens_estimated": bool(quality_metrics.get("tokens_estimated", False)),
    }


def _normalize_react_record(
    *,
    model_name: str,
    scenario: BenchmarkScenario,
    quality_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Приводит запись ReAct к общему формату benchmark."""
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "system": "react",
        "model": model_name,
        "scenario_name": scenario.name,
        "user_query": scenario.query,
        "latency_sec": float(quality_metrics.get("response_time_sec", 0.0)),
        "scenario_success": bool(quality_metrics.get("scenario_success", False)),
        "error_count": int(quality_metrics.get("error_count_final", 0)),
        "expected_servers": list(quality_metrics.get("expected_servers", [])),
        "used_servers": list(quality_metrics.get("used_servers", [])),
        "tool_selection_correct": bool(quality_metrics.get("tool_selection_correct", False)),
        "mcp_calls_count": int(quality_metrics.get("mcp_calls_count", 0)),
        "tokens_prompt": int(quality_metrics.get("tokens_prompt", 0)),
        "tokens_completion": int(quality_metrics.get("tokens_completion", 0)),
        "tokens_total": int(quality_metrics.get("tokens_total", 0)),
        "tokens_estimated": bool(quality_metrics.get("tokens_estimated", False)),
    }


def _failure_benchmark_record(
    *,
    system: str,
    model_name: str,
    scenario: BenchmarkScenario,
    error: BaseException,
    latency_sec: float,
) -> dict[str, Any]:
    """Запись для ячейки матрицы, где вызов упал с исключением (API, лимиты и т.д.)."""
    base: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "system": system,
        "model": model_name,
        "scenario_name": scenario.name,
        "user_query": scenario.query,
        "latency_sec": float(latency_sec),
        "scenario_success": False,
        "error_count": 1,
        "expected_servers": sorted(scenario.expected_servers),
        "used_servers": [],
        "tool_selection_correct": False,
        "mcp_calls_count": 0,
        "tokens_prompt": 0,
        "tokens_completion": 0,
        "tokens_total": 0,
        "tokens_estimated": False,
        "run_failed": True,
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    return base


async def run_benchmark_matrix(
    *,
    models: list[str],
    scenarios: list[BenchmarkScenario],
    systems: list[str],
    output_path: str,
) -> list[dict[str, Any]]:
    """Прогоняет матрицу model x scenario x system и пишет JSONL."""
    records: list[dict[str, Any]] = []
    destination = Path(output_path)
    if destination.exists():
        destination.unlink()

    for model_name in models:
        with override_yandex_model(model_name):
            for scenario in scenarios:
                if "orchestrator" in systems:
                    orch_started = time.perf_counter()
                    try:
                        orchestrator_state = await run_query(
                            scenario.query,
                            scenario_name=scenario.name,
                            expected_servers=scenario.expected_servers,
                            persist_metrics=False,
                        )
                        orch_metrics = dict(orchestrator_state.get("quality_metrics", {}))
                        orch_record = _normalize_orchestrator_record(
                            model_name=model_name,
                            scenario=scenario,
                            quality_metrics=orch_metrics,
                        )
                        _append_jsonl_record(destination, orch_record)
                        records.append(orch_record)
                    except Exception as exc:
                        elapsed = time.perf_counter() - orch_started
                        log.error(
                            "benchmark_run_failed",
                            system="orchestrator",
                            model=model_name,
                            scenario_name=scenario.name,
                            error=str(exc),
                        )
                        orch_record = _failure_benchmark_record(
                            system="orchestrator",
                            model_name=model_name,
                            scenario=scenario,
                            error=exc,
                            latency_sec=elapsed,
                        )
                        _append_jsonl_record(destination, orch_record)
                        records.append(orch_record)

                if "react" in systems:
                    react_started = time.perf_counter()
                    try:
                        react_result = await run_react_query(
                            scenario.query,
                            scenario_name=scenario.name,
                            expected_servers=scenario.expected_servers,
                        )
                        react_metrics = dict(react_result.get("quality_metrics", {}))
                        react_record = _normalize_react_record(
                            model_name=model_name,
                            scenario=scenario,
                            quality_metrics=react_metrics,
                        )
                        _append_jsonl_record(destination, react_record)
                        records.append(react_record)
                    except Exception as exc:
                        elapsed = time.perf_counter() - react_started
                        log.error(
                            "benchmark_run_failed",
                            system="react",
                            model=model_name,
                            scenario_name=scenario.name,
                            error=str(exc),
                        )
                        react_record = _failure_benchmark_record(
                            system="react",
                            model_name=model_name,
                            scenario=scenario,
                            error=exc,
                            latency_sec=elapsed,
                        )
                        _append_jsonl_record(destination, react_record)
                        records.append(react_record)

    return records


def load_benchmark_records(metrics_path: str) -> list[dict[str, Any]]:
    """Читает benchmark JSONL в список записей."""
    path = Path(metrics_path)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            raw = line.strip()
            if not raw:
                continue
            records.append(json.loads(raw))
    return records


def _aggregate_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Агрегирует метрики группы записей."""
    latencies = [float(item.get("latency_sec", 0.0)) for item in records]
    success_values = [1.0 if item.get("scenario_success") else 0.0 for item in records]
    tool_correct = [1.0 if item.get("tool_selection_correct") else 0.0 for item in records]
    errors = [int(item.get("error_count", 0)) for item in records]
    tokens = [int(item.get("tokens_total", 0)) for item in records]
    estimated = [1.0 if item.get("tokens_estimated") else 0.0 for item in records]
    return {
        "runs": len(records),
        "latency_avg_sec": round(mean(latencies), 6) if latencies else 0.0,
        "latency_p50_sec": round(median(latencies), 6) if latencies else 0.0,
        "latency_p95_sec": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 6)
        if latencies
        else 0.0,
        "success_rate": round(mean(success_values), 6) if success_values else 0.0,
        "tool_selection_correct_rate": round(mean(tool_correct), 6) if tool_correct else 0.0,
        "errors_avg": round(mean(errors), 6) if errors else 0.0,
        "tokens_total_avg": round(mean(tokens), 6) if tokens else 0.0,
        "tokens_estimated_share": round(mean(estimated), 6) if estimated else 0.0,
    }


def build_benchmark_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Строит сводку по всем записям, по системам и по моделям."""
    summary: dict[str, Any] = {
        "records_count": len(records),
        "overall": _aggregate_group(records) if records else {},
        "by_system": {},
        "by_model": {},
    }
    systems = sorted({str(item.get("system", "")) for item in records})
    for system_name in systems:
        system_records = [item for item in records if item.get("system") == system_name]
        summary["by_system"][system_name] = _aggregate_group(system_records)
    models = sorted({str(item.get("model", "")) for item in records})
    for model_name in models:
        model_records = [item for item in records if item.get("model") == model_name]
        summary["by_model"][model_name] = _aggregate_group(model_records)
    return summary


def build_benchmark_summary_from_file(metrics_path: str) -> dict[str, Any]:
    """Читает JSONL-файл benchmark и строит агрегированный отчёт."""
    records = load_benchmark_records(metrics_path)
    result = build_benchmark_summary(records)
    result["metrics_path"] = metrics_path
    return result
