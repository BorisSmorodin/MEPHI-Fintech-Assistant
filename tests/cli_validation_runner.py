"""CLI runner для проверки пользовательских сценариев с фиксированной кодировкой."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_EXE = ROOT / ".venv" / "Scripts" / "python.exe"
OUTPUT_PATH = ROOT / "data" / "fixtures" / "cli_validation_results_utf8.json"

SMOKE_COMMANDS = [
    "\u041f\u043e\u043a\u0430\u0436\u0438 \u043a\u043e\u0442\u0438\u0440\u043e\u0432\u043a\u0443 SBER",
    "debug",
    "clear",
    "debug",
    "exit",
]

MANDATORY_SCENARIOS = [
    ("scenario_1_market", "\u041f\u043e\u043a\u0430\u0436\u0438 \u043a\u043e\u0442\u0438\u0440\u043e\u0432\u043a\u0443 SBER"),
    (
        "scenario_2_news",
        "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u043d\u043e\u0432\u043e\u0441\u0442\u0438 "
        "\u043f\u043e \u0413\u0430\u0437\u043f\u0440\u043e\u043c\u0443",
    ),
    (
        "scenario_3_risk",
        "\u041e\u0446\u0435\u043d\u0438 \u0440\u0438\u0441\u043a \u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f "
        "demo_portfolio",
    ),
    (
        "scenario_4_stress",
        "\u041f\u0440\u043e\u0432\u0435\u0434\u0438 \u0441\u0442\u0440\u0435\u0441\u0441-\u0442\u0435\u0441\u0442 "
        "\u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f demo_portfolio \u043f\u0440\u0438 "
        "\u043f\u0430\u0434\u0435\u043d\u0438\u0438 IMOEX \u043d\u0430 20%",
    ),
    (
        "scenario_5_complex",
        "\u041e\u0446\u0435\u043d\u0438 \u0440\u0438\u0441\u043a \u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f "
        "demo_portfolio \u0441 \u0443\u0447\u0435\u0442\u043e\u043c \u043d\u043e\u0432\u043e\u0441\u0442\u0435\u0439 "
        "\u043d\u0435\u0444\u0442\u0435\u0433\u0430\u0437\u0430 \u0438 \u043a\u043e\u0442\u0438\u0440\u043e\u0432\u043a\u0438 "
        "GAZP",
    ),
]

FALLBACK_SCENARIOS = [
    (
        "fallback_news_partial",
        "\u041a\u0430\u043a\u0438\u0435 \u043d\u043e\u0432\u043e\u0441\u0442\u0438 \u043f\u043e "
        "\u0413\u0430\u0437\u043f\u0440\u043e\u043c\u0443 \u0437\u0430 \u0441\u0435\u0433\u043e\u0434\u043d\u044f?",
    ),
    (
        "fallback_analytics_error",
        "\u041e\u0446\u0435\u043d\u0438 \u0440\u0438\u0441\u043a \u043f\u043e\u0440\u0442\u0444\u0435\u043b\u044f "
        "unknown_portfolio",
    ),
]


def _run_cli_session(name: str, commands: list[str]) -> dict[str, object]:
    session_input = "\n".join(commands) + "\n"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        completed = subprocess.run(
            [str(PYTHON_EXE), "-m", "ui.cli"],
            cwd=str(ROOT),
            input=session_input,
            text=True,
            capture_output=True,
            timeout=240,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return {
            "name": name,
            "commands": commands,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as error:
        return {
            "name": name,
            "commands": commands,
            "returncode": None,
            "stdout": error.stdout or "",
            "stderr": error.stderr or "",
            "timed_out": True,
        }


def main() -> int:
    results: list[dict[str, object]] = []
    results.append(_run_cli_session("smoke_cli_commands", SMOKE_COMMANDS))

    for scenario_name, query in MANDATORY_SCENARIOS:
        results.append(_run_cli_session(scenario_name, [query, "exit"]))

    for scenario_name, query in FALLBACK_SCENARIOS:
        results.append(_run_cli_session(scenario_name, [query, "exit"]))

    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = [
        {
            "name": item["name"],
            "returncode": item["returncode"],
            "timed_out": item["timed_out"],
        }
        for item in results
    ]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved_to={OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
