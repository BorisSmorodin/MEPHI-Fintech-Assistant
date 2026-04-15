"""Утилита построения сводного отчета по метрикам качества."""

from __future__ import annotations

import argparse
import json

from orchestrator.quality_metrics import build_quality_metrics_report


def main() -> None:
    """Точка входа CLI-утилиты."""
    parser = argparse.ArgumentParser(description="Build quality metrics summary report.")
    parser.add_argument(
        "--metrics-path",
        default="data/fixtures/quality_metrics.jsonl",
        help="Путь к JSONL-файлу с метриками сценариев.",
    )
    args = parser.parse_args()
    report = build_quality_metrics_report(args.metrics_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
