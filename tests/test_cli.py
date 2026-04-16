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

    def _fake_execute_query_sync(
        _query: str,
        *,
        state_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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


def test_react_query_mode_prints_output(monkeypatch) -> None:
    """Одноразовый режим ReAct: мок исполнения, проверка вывода."""
    output: list[str] = []

    def _fake_execute_react(
        _query: str,
        *,
        recursion_limit: int | None = None,
    ) -> dict[str, Any]:
        return {
            "tool_trace": [{"tool_name": "get_stock_quote", "tool_args": {"ticker": "SBER"}}],
            "final_answer": "Готово.",
            "mcp_calls_count": 1,
        }

    monkeypatch.setattr(cli, "execute_react_query_sync", _fake_execute_react)
    monkeypatch.setattr(cli.typer, "echo", lambda m: output.append(str(m)))

    cli.react_e2e(query="котировка SBER")

    joined = "\n".join(output)
    assert "tool_trace" in joined
    assert "get_stock_quote" in joined
    assert "Готово." in joined
    assert "mcp_calls_count=1" in joined


def test_react_repl_debug_and_clear(monkeypatch) -> None:
    """REPL ReAct: debug до/после clear."""
    inputs = iter(
        [
            "debug",
            "тестовый запрос",
            "debug",
            "clear",
            "debug",
            "exit",
        ]
    )
    output: list[str] = []

    def _fake_prompt(_text: str) -> str:
        return next(inputs)

    def _fake_execute_react(
        _query: str,
        *,
        recursion_limit: int | None = None,
    ) -> dict[str, Any]:
        return {
            "tool_trace": [{"tool_name": "x"}],
            "final_answer": "ответ",
            "mcp_calls_count": 1,
        }

    monkeypatch.setattr(cli.typer, "prompt", _fake_prompt)
    monkeypatch.setattr(cli, "execute_react_query_sync", _fake_execute_react)
    monkeypatch.setattr(cli.typer, "echo", lambda m: output.append(str(m)))
    monkeypatch.setattr(cli.typer, "clear", lambda: None)

    cli.react_e2e(query=None)

    joined = "\n".join(output)
    assert joined.count("пока нет завершённых") >= 2
    assert "tool_trace steps: 1" in joined
    assert "Контекст ReAct очищен." in joined
    assert "Завершение сессии ReAct." in joined
