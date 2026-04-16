"""Тесты CLI-команд и сброса состояния REPL."""

from __future__ import annotations

from typing import Any

from ui import cli


def test_chat_clear_resets_last_state(monkeypatch) -> None:
    """Проверяет, что clear действительно сбрасывает debug-состояние."""
    inputs = iter(
        [
            "Покажи котировку SBER",
            "debug",
            "clear",
            "debug",
            "exit",
        ]
    )
    output: list[str] = []

    def _fake_prompt(_text: str) -> str:
        return next(inputs)

    def _fake_execute_query_sync(_query: str) -> dict[str, Any]:
        return {
            "query_type": "market_monitor",
            "error_count": 0,
            "warnings": [],
            "plan": [{"step_number": 1}],
            "current_step": 1,
            "final_answer": "Тестовый ответ.",
        }

    def _fake_echo(message: str) -> None:
        output.append(message)

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)
    monkeypatch.setattr(cli, "execute_query_sync", _fake_execute_query_sync)
    monkeypatch.setattr(cli.typer, "echo", _fake_echo)
    monkeypatch.setattr(cli.typer, "clear", lambda: None)

    cli.chat()

    debug_messages = [row for row in output if "Debug" in row]
    assert any("query_type: market_monitor" in row for row in debug_messages)
    assert any("пока нет выполненных запросов" in row for row in debug_messages)
    assert "Контекст очищен." in output
