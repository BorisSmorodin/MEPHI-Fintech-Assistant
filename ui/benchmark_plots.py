"""Визуализации по JSONL benchmark (latency, токены, успешность, heatmap по сценариям)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ui.benchmark_catalog import DEFAULT_BENCHMARK_SCENARIOS
from ui.benchmark_runner import load_benchmark_records


def _scenario_order() -> list[str]:
    """Порядок сценариев как в каталоге."""
    return [s.name for s in DEFAULT_BENCHMARK_SCENARIOS]


def _short_model_label(model: str, max_len: int = 22) -> str:
    """Короткое имя модели для подписей осей."""
    base = model.replace("/latest", "").strip()
    if len(base) > max_len:
        return base[: max_len - 1] + "…"
    return base


def _records_by(records: list[dict[str, Any]], *, system: str) -> list[dict[str, Any]]:
    return [r for r in records if r.get("system") == system]


def _is_failed(r: dict[str, Any]) -> bool:
    return bool(r.get("run_failed"))


def render_benchmark_plots(
    records: list[dict[str, Any]],
    output_dir: Path,
    *,
    dpi: int = 120,
) -> list[Path]:
    """Строит PNG-графики в output_dir. Возвращает список созданных файлов."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    scenarios = _scenario_order()
    models = sorted({str(r.get("model", "")) for r in records if r.get("model")})
    if not models:
        return []

    x_models = np.arange(len(models))
    width = 0.36

    # --- Средняя latency по системам (по всем сценариям) ---
    orch_lat = []
    react_lat = []
    for m in models:
        o = _records_by(records, system="orchestrator")
        r_ = _records_by(records, system="react")
        o_m = [float(x.get("latency_sec", 0)) for x in o if x.get("model") == m and not _is_failed(x)]
        r_m = [float(x.get("latency_sec", 0)) for x in r_ if x.get("model") == m and not _is_failed(x)]
        orch_lat.append(float(np.mean(o_m)) if o_m else 0.0)
        react_lat.append(float(np.mean(r_m)) if r_m else 0.0)

    fig, ax = plt.subplots(figsize=(max(10, len(models) * 0.9), 5.5))
    ax.bar(x_models - width / 2, orch_lat, width, label="orchestrator")
    ax.bar(x_models + width / 2, react_lat, width, label="react")
    ax.set_ylabel("Средняя latency, с")
    ax.set_title("Средняя задержка ответа по моделям")
    ax.set_xticks(x_models)
    ax.set_xticklabels([_short_model_label(m) for m in models], rotation=35, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.35)
    fig.tight_layout()
    p = output_dir / "benchmark_latency_mean_by_model.png"
    fig.savefig(p, dpi=dpi)
    plt.close(fig)
    written.append(p)

    # --- Средние токены (пропускаем run_failed с нулевыми токенами) ---
    def _mean_tokens(rows: list[dict[str, Any]]) -> float:
        vals: list[int] = []
        for r in rows:
            t = int(r.get("tokens_total", 0))
            if _is_failed(r) and t == 0:
                continue
            vals.append(t)
        if not vals:
            return 0.0
        return float(np.mean(vals))

    orch_tok = []
    react_tok = []
    for m in models:
        o = [r for r in _records_by(records, system="orchestrator") if r.get("model") == m]
        r_ = [r for r in _records_by(records, system="react") if r.get("model") == m]
        orch_tok.append(_mean_tokens(o))
        react_tok.append(_mean_tokens(r_))

    fig, ax = plt.subplots(figsize=(max(10, len(models) * 0.9), 5.5))
    ax.bar(x_models - width / 2, orch_tok, width, label="orchestrator")
    ax.bar(x_models + width / 2, react_tok, width, label="react")
    ax.set_ylabel("Среднее tokens_total")
    ax.set_title("Средний объём токенов по моделям")
    ax.set_xticks(x_models)
    ax.set_xticklabels([_short_model_label(m) for m in models], rotation=35, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.35)
    fig.tight_layout()
    p = output_dir / "benchmark_tokens_mean_by_model.png"
    fig.savefig(p, dpi=dpi)
    plt.close(fig)
    written.append(p)

    # --- Доли успеха и корректности инструментов по модели (все прогоны модели) ---
    succ = []
    tools = []
    for m in models:
        mrec = [r for r in records if r.get("model") == m]
        if not mrec:
            succ.append(0.0)
            tools.append(0.0)
            continue
        succ.append(sum(1 for r in mrec if r.get("scenario_success")) / len(mrec))
        tools.append(sum(1 for r in mrec if r.get("tool_selection_correct")) / len(mrec))

    fig, ax = plt.subplots(figsize=(max(10, len(models) * 0.9), 5))
    ax.bar(x_models - width / 2, succ, width, label="scenario_success")
    ax.bar(x_models + width / 2, tools, width, label="tool_selection_correct")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Доля")
    ax.set_title("Успешность сценария и выбор инструментов по моделям")
    ax.set_xticks(x_models)
    ax.set_xticklabels([_short_model_label(m) for m in models], rotation=35, ha="right")
    ax.legend()
    ax.grid(axis="y", alpha=0.35)
    fig.tight_layout()
    p = output_dir / "benchmark_success_and_tools_by_model.png"
    fig.savefig(p, dpi=dpi)
    plt.close(fig)
    written.append(p)

    # --- Heatmap latency: модель × сценарий ---
    def heatmap(system: str, fname: str, title: str) -> None:
        mat = np.full((len(models), len(scenarios)), np.nan)
        for i, m in enumerate(models):
            for j, sc in enumerate(scenarios):
                row = next(
                    (
                        r
                        for r in records
                        if r.get("model") == m
                        and r.get("scenario_name") == sc
                        and r.get("system") == system
                    ),
                    None,
                )
                if row is None:
                    continue
                if _is_failed(row):
                    mat[i, j] = np.nan
                else:
                    mat[i, j] = float(row.get("latency_sec", 0.0))
        fig, ax = plt.subplots(figsize=(max(8, len(scenarios) * 1.4), max(6, len(models) * 0.45)))
        im = ax.imshow(mat, aspect="auto", cmap="YlOrRd")
        ax.set_xticks(np.arange(len(scenarios)))
        ax.set_xticklabels(scenarios, rotation=30, ha="right")
        ax.set_yticks(np.arange(len(models)))
        ax.set_yticklabels([_short_model_label(m) for m in models])
        ax.set_title(title)
        fig.colorbar(im, ax=ax, label="latency, с")
        # подписи в ячейках
        for i in range(len(models)):
            for j in range(len(scenarios)):
                v = mat[i, j]
                if np.isnan(v):
                    ax.text(j, i, "—", ha="center", va="center", color="0.35", fontsize=7)
                else:
                    ax.text(j, i, f"{v:.1f}", ha="center", va="center", color="0.1", fontsize=7)
        fig.tight_layout()
        out = output_dir / fname
        fig.savefig(out, dpi=dpi)
        plt.close(fig)
        written.append(out)

    heatmap("orchestrator", "benchmark_latency_heatmap_orchestrator.png", "Latency: orchestrator")
    heatmap("react", "benchmark_latency_heatmap_react.png", "Latency: ReAct")

    return written


def render_benchmark_plots_from_file(metrics_path: str, output_dir: str, *, dpi: int = 120) -> list[Path]:
    """Загружает JSONL и сохраняет графики в output_dir."""
    records = load_benchmark_records(metrics_path)
    return render_benchmark_plots(records, Path(output_dir), dpi=dpi)
