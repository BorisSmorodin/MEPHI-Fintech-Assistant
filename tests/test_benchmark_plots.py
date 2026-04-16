"""Тесты визуализаций benchmark."""

from __future__ import annotations

import json

from ui.benchmark_plots import render_benchmark_plots


def test_render_benchmark_plots_writes_png(tmp_path) -> None:
    """Smoke: несколько записей → PNG в каталоге."""
    records = [
        {
            "system": "orchestrator",
            "model": "m-a/latest",
            "scenario_name": "market_only",
            "latency_sec": 1.0,
            "scenario_success": True,
            "tool_selection_correct": True,
            "tokens_total": 100,
        },
        {
            "system": "react",
            "model": "m-a/latest",
            "scenario_name": "market_only",
            "latency_sec": 2.0,
            "scenario_success": True,
            "tool_selection_correct": True,
            "tokens_total": 200,
        },
    ]
    out = render_benchmark_plots(records, tmp_path, dpi=72)
    assert len(out) >= 4
    assert all(p.suffix == ".png" for p in out)
    assert all(p.exists() for p in out)


def test_render_benchmark_plots_empty_records(tmp_path) -> None:
    """Пустой список — без файлов."""
    assert render_benchmark_plots([], tmp_path) == []
