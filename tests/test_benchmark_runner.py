"""Тесты benchmark runner и метрик ReAct."""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import AIMessage

from react_agent.quality_metrics import (
    collect_react_quality_metrics,
    extract_react_token_usage,
    used_servers_from_tool_trace,
)
from ui.benchmark_catalog import BenchmarkScenario
from ui.benchmark_runner import (
    build_benchmark_summary,
    load_benchmark_records,
    override_yandex_model,
    run_benchmark_matrix,
)


def test_used_servers_from_tool_trace() -> None:
    """Проверяет маппинг tool_name -> server."""
    trace = [
        {"tool_name": "get_stock_quote"},
        {"tool_name": "fetch_news"},
        {"tool_name": "calculate_risk_metrics"},
    ]
    assert used_servers_from_tool_trace(trace) == {"market", "news", "analytics"}


def test_extract_react_token_usage_fallback_estimation() -> None:
    """Если usage_metadata нет, должна включаться оценка токенов."""
    usage = extract_react_token_usage(
        [AIMessage(content="Итог без usage metadata")],
        user_query="Покажи SBER",
        final_answer="Котировка получена",
    )
    assert usage["tokens_total"] > 0
    assert usage["tokens_estimated"] is True


def test_collect_react_quality_metrics_shape() -> None:
    """Проверяет структуру совместимых quality-metrics для ReAct."""
    record = collect_react_quality_metrics(
        scenario_name="market_only",
        user_query="Покажи SBER",
        expected_servers={"market"},
        response_time_sec=0.5,
        tool_trace=[{"tool_name": "get_stock_quote", "result_preview": "ok"}],
        final_answer="Готово",
        mcp_calls_count=1,
        messages=[AIMessage(content="Готово")],
    )
    assert record["tool_selection_correct"] is True
    assert record["mcp_calls_count"] == 1
    assert "tokens_total" in record


def test_override_yandex_model_context_restores_env(monkeypatch) -> None:
    """Проверяет временное переопределение YANDEX_CLOUD_MODEL."""
    monkeypatch.setenv("YANDEX_CLOUD_MODEL", "before-model/latest")
    with override_yandex_model("during-model/latest"):
        assert os.environ.get("YANDEX_CLOUD_MODEL") == "during-model/latest"
    assert os.environ.get("YANDEX_CLOUD_MODEL") == "before-model/latest"


def test_build_benchmark_summary() -> None:
    """Проверяет агрегацию по системам и моделям."""
    records = [
        {
            "system": "orchestrator",
            "model": "m1",
            "latency_sec": 1.0,
            "scenario_success": True,
            "tool_selection_correct": True,
            "error_count": 0,
            "tokens_total": 100,
            "tokens_estimated": False,
        },
        {
            "system": "react",
            "model": "m1",
            "latency_sec": 3.0,
            "scenario_success": False,
            "tool_selection_correct": False,
            "error_count": 2,
            "tokens_total": 120,
            "tokens_estimated": True,
        },
    ]
    summary = build_benchmark_summary(records)
    assert summary["records_count"] == 2
    assert "orchestrator" in summary["by_system"]
    assert "m1" in summary["by_model"]


@pytest.mark.asyncio
async def test_run_benchmark_matrix_with_mocks(tmp_path, monkeypatch) -> None:
    """Smoke для runner без реальных MCP/LLM через monkeypatch."""
    scenario = BenchmarkScenario(
        name="market_only",
        query="Покажи SBER",
        expected_servers={"market"},
    )

    async def _fake_run_query(*_args, **_kwargs):
        return {
            "quality_metrics": {
                "response_time_sec": 0.1,
                "scenario_success": True,
                "error_count_final": 0,
                "expected_servers": ["market"],
                "observed_servers": ["market"],
                "tool_selection_correct": True,
                "mcp_calls_count": 1,
                "tokens_prompt": 10,
                "tokens_completion": 5,
                "tokens_total": 15,
                "tokens_estimated": False,
            }
        }

    async def _fake_run_react_query(*_args, **_kwargs):
        return {
            "quality_metrics": {
                "response_time_sec": 0.2,
                "scenario_success": True,
                "error_count_final": 0,
                "expected_servers": ["market"],
                "used_servers": ["market"],
                "tool_selection_correct": True,
                "mcp_calls_count": 1,
                "tokens_prompt": 8,
                "tokens_completion": 4,
                "tokens_total": 12,
                "tokens_estimated": True,
            }
        }

    monkeypatch.setattr("ui.benchmark_runner.run_query", _fake_run_query)
    monkeypatch.setattr("ui.benchmark_runner.run_react_query", _fake_run_react_query)

    output_path = tmp_path / "bench.jsonl"
    records = await run_benchmark_matrix(
        models=["aliceai-llm/latest"],
        scenarios=[scenario],
        systems=["orchestrator", "react"],
        output_path=str(output_path),
    )
    assert len(records) == 2
    loaded = load_benchmark_records(str(output_path))
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_run_benchmark_matrix_continues_after_cell_failure(tmp_path, monkeypatch) -> None:
    """Ошибка в одной ячейке логируется, JSONL получает run_failed, матрица продолжается."""
    scenario = BenchmarkScenario(
        name="market_only",
        query="Покажи SBER",
        expected_servers={"market"},
    )
    calls = {"orch": 0}

    async def _failing_run_query(*_args, **_kwargs):
        calls["orch"] += 1
        raise RuntimeError("simulated API limit")

    async def _ok_run_react_query(*_args, **_kwargs):
        return {
            "quality_metrics": {
                "response_time_sec": 0.2,
                "scenario_success": True,
                "error_count_final": 0,
                "expected_servers": ["market"],
                "used_servers": ["market"],
                "tool_selection_correct": True,
                "mcp_calls_count": 1,
                "tokens_prompt": 8,
                "tokens_completion": 4,
                "tokens_total": 12,
                "tokens_estimated": True,
            }
        }

    monkeypatch.setattr("ui.benchmark_runner.run_query", _failing_run_query)
    monkeypatch.setattr("ui.benchmark_runner.run_react_query", _ok_run_react_query)

    output_path = tmp_path / "bench.jsonl"
    records = await run_benchmark_matrix(
        models=["aliceai-llm/latest"],
        scenarios=[scenario],
        systems=["orchestrator", "react"],
        output_path=str(output_path),
    )
    assert len(records) == 2
    assert records[0].get("run_failed") is True
    assert "simulated API limit" in str(records[0].get("error_message", ""))
    assert records[1].get("run_failed") is None
    assert records[1].get("scenario_success") is True
    loaded = load_benchmark_records(str(output_path))
    assert len(loaded) == 2
