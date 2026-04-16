"""CLI-интерфейс инвестиционного ассистента."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import structlog
import typer

from orchestrator.graph import run_query

log = structlog.get_logger()
app = typer.Typer(help="Консольный интерфейс Investment Assistant.")


@dataclass(slots=True)
class CLICommandResult:
    """Результат обработки пользовательской команды REPL."""

    action: str
    message: str | None = None


def execute_query_sync(user_query: str) -> dict[str, Any]:
    """Выполняет запрос к оркестратору в синхронной оболочке."""
    log.info("cli_query_execute", user_query=user_query)
    return asyncio.run(run_query(user_query))


def format_debug_payload(state: dict[str, Any] | None) -> str:
    """Формирует текст debug-диагностики для последнего состояния."""
    if not state:
        return "Debug: пока нет выполненных запросов."

    plan = state.get("plan", [])
    return "\n".join(
        [
            "Debug summary:",
            f"- query_type: {state.get('query_type', 'n/a')}",
            f"- error_count: {state.get('error_count', 0)}",
            f"- warnings_count: {len(state.get('warnings', []))}",
            f"- plan_steps: {len(plan)}",
            f"- current_step: {state.get('current_step', 0)}",
        ]
    )


def process_cli_input(user_input: str, last_state: dict[str, Any] | None) -> CLICommandResult:
    """Обрабатывает REPL-команду пользователя без запуска оркестратора."""
    normalized = user_input.strip()
    if not normalized:
        return CLICommandResult(action="noop")
    command = normalized.lower()
    if command == "exit":
        return CLICommandResult(action="exit")
    if command == "clear":
        return CLICommandResult(action="clear")
    if command == "debug":
        return CLICommandResult(action="debug", message=format_debug_payload(last_state))
    return CLICommandResult(action="query", message=normalized)


@app.command()
def chat() -> None:
    """Запускает REPL-чат для работы с оркестратором."""
    typer.echo("Investment Assistant CLI. Команды: exit, clear, debug.")
    last_state: dict[str, Any] | None = None

    while True:
        user_input = typer.prompt(">>>")
        command_result = process_cli_input(user_input, last_state)

        if command_result.action == "noop":
            continue
        if command_result.action == "exit":
            typer.echo("Завершение сессии.")
            break
        if command_result.action == "clear":
            last_state = None
            typer.clear()
            typer.echo("Контекст очищен.")
            continue
        if command_result.action == "debug":
            typer.echo(command_result.message or "Debug: нет данных.")
            continue

        try:
            state = execute_query_sync(command_result.message or "")
            last_state = state
            final_answer = str(state.get("final_answer") or "").strip()
            if final_answer:
                typer.echo(final_answer)
            else:
                typer.echo("Не удалось сформировать итоговый ответ. Попробуйте уточнить запрос.")
        except ValueError as error:
            typer.echo(f"Ошибка валидации запроса: {error}")
        except Exception as error:
            log.error("cli_query_failed", error=str(error))
            typer.echo("Произошла ошибка при обработке запроса. Попробуйте снова.")


if __name__ == "__main__":
    app()

