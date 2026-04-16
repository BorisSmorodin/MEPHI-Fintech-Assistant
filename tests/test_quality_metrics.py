"""Тесты сбора и агрегации метрик качества сценариев."""

from __future__ import annotations

import json

from orchestrator.quality_metrics import (
    append_quality_metric,
    build_quality_metrics_report,
    collect_quality_metrics,
)


def test_collect_quality_metrics_has_required_fields() -> None:
    """Проверяет обязательные поля метрик качества."""
    state = {
        "query_type": "market_monitor",
        "plan": [
            {
                "step_number": 1,
                "description": "quote",
                "target_server": "market_executor",
                "tool_name": "get_stock_quote",
                "tool_args": {"ticker": "SBER"},
            },
            {
                "step_number": 2,
                "description": "summary",
                "target_server": "summarizer",
                "tool_name": "summarize",
                "tool_args": {},
            },
        ],
        "current_step": 1,
        "final_answer": "Ответ готов.",
        "error_count": 0,
    }
    record = collect_quality_metrics(
        state=state,
        user_query="Покажи котировку SBER",
        scenario_name="market_only",
        response_time_sec=0.123,
        expected_servers={"market"},
    )
    required_fields = {
        "scenario_success",
        "tool_selection_correct",
        "tool_selection_correct_full_plan",
        "response_time_sec",
        "mcp_calls_count",
        "error_count_final",
        "llm_plan_used",
        "llm_plan_parse_failed",
        "plan_contract_ok",
        "plan_repaired",
        "routing_failure_reason",
        "tokens_prompt",
        "tokens_completion",
        "tokens_total",
        "tokens_estimated",
    }
    assert required_fields <= set(record.keys())
    assert record["tool_selection_correct"] is True
    assert record["tool_selection_correct_full_plan"] is True
    assert record["mcp_calls_count"] == 1


def test_collect_quality_metrics_estimates_tokens_when_planner_usage_missing() -> None:
    """Если в state нет usage от LLM, токены оцениваются по запросу и ответу."""
    state = {
        "query_type": "market_monitor",
        "plan": [
            {
                "step_number": 1,
                "description": "quote",
                "target_server": "market_executor",
                "tool_name": "get_stock_quote",
                "tool_args": {"ticker": "SBER"},
            },
        ],
        "current_step": 1,
        "final_answer": "Краткий ответ пользователю.",
        "error_count": 0,
    }
    record = collect_quality_metrics(
        state=state,
        user_query="Покажи котировку SBER",
        scenario_name="market_only",
        response_time_sec=0.1,
        expected_servers={"market"},
    )
    assert record["tokens_estimated"] is True
    assert record["planner_tokens_estimated"] is True
    assert int(record["tokens_total"]) > 0
    assert int(record["tokens_prompt"]) > 0


def test_append_and_build_quality_metrics_report(tmp_path) -> None:
    """Проверяет JSONL-логирование и сводный отчет."""
    metrics_path = tmp_path / "quality_metrics.jsonl"
    first = {
        "timestamp_utc": "2026-01-01T00:00:00+00:00",
        "scenario_name": "a",
        "scenario_success": True,
        "tool_selection_correct": True,
        "response_time_sec": 1.0,
        "mcp_calls_count": 2,
        "error_count_final": 0,
    }
    second = {
        "timestamp_utc": "2026-01-01T00:01:00+00:00",
        "scenario_name": "b",
        "scenario_success": False,
        "tool_selection_correct": True,
        "response_time_sec": 3.0,
        "mcp_calls_count": 4,
        "error_count_final": 1,
        "planner_total_tokens": 100,
        "planner_tokens_estimated": True,
    }
    append_quality_metric(first, str(metrics_path))
    append_quality_metric(second, str(metrics_path))

    lines = metrics_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["scenario_name"] == "a"

    report = build_quality_metrics_report(str(metrics_path))
    assert report["records_count"] == 2
    assert report["scenario_success_rate"] == 0.5
    assert report["tool_selection_correct_rate"] == 1.0
    assert report["avg_response_time_sec"] == 2.0
    assert report["avg_planner_total_tokens"] == 50.0
    assert report["estimated_tokens_share"] == 0.5
