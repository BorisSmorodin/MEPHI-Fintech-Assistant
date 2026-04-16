"""CLI-интерфейс инвестиционного ассистента."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import structlog
import typer

from orchestrator.graph import run_query
from ui.benchmark_catalog import DEFAULT_BENCHMARK_MODELS, DEFAULT_BENCHMARK_SCENARIOS
from ui.benchmark_runner import build_benchmark_summary_from_file, run_benchmark_matrix

log = structlog.get_logger()
app = typer.Typer(help="Консольный интерфейс Investment Assistant.")


@dataclass(slots=True)
class CLICommandResult:
    """Результат обработки пользовательской команды REPL."""

    action: str
    message: str | None = None


def execute_query_sync(
    user_query: str,
    *,
    state_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Выполняет запрос к оркестратору в синхронной оболочке."""
    log.info("cli_query_execute", user_query=user_query)
    return asyncio.run(run_query(user_query, state_overrides=state_overrides))


def execute_react_query_sync(
    user_query: str,
    *,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """Запускает ReAct-агент (MCP tools) в синхронной оболочке для e2e и CLI."""
    from react_agent.graph import run_react_query

    log.info("cli_react_execute", user_query=user_query, recursion_limit=recursion_limit)
    return asyncio.run(run_react_query(user_query, recursion_limit=recursion_limit))


def format_react_cli_output(result: dict[str, Any]) -> str:
    """Форматирует результат run_react_query для вывода в терминал."""
    trace = result.get("tool_trace", [])
    trace_text = json.dumps(trace, ensure_ascii=False, indent=2)
    final_answer = str(result.get("final_answer") or "").strip() or "(пусто)"
    mcp_calls = int(result.get("mcp_calls_count", 0))
    return "\n".join(
        [
            "--- tool_trace ---",
            trace_text,
            "--- final_answer ---",
            final_answer,
            f"--- mcp_calls_count={mcp_calls} ---",
        ]
    )


def format_react_debug_payload(last_react: dict[str, Any] | None) -> str:
    """Краткая диагностика последнего прогона ReAct."""
    if not last_react:
        return "Debug ReAct: пока нет завершённых запросов."
    trace = last_react.get("tool_trace", [])
    preview = str(last_react.get("final_answer") or "")[:240]
    return "\n".join(
        [
            "Debug ReAct:",
            f"- tool_trace steps: {len(trace)}",
            f"- mcp_calls_count: {last_react.get('mcp_calls_count', 0)}",
            f"- final_answer preview: {preview!r}",
        ]
    )


def _answer_depth_overrides(answer_depth: str | None) -> dict[str, Any] | None:
    """Преобразует опцию CLI в state_overrides для summarizer."""
    normalized = (answer_depth or "").strip().lower()
    if normalized in ("", "auto"):
        return None
    if normalized == "compact":
        return {"answer_depth": "compact"}
    if normalized == "standard":
        return {"answer_depth": "standard"}
    raise typer.BadParameter("ожидается auto, compact или standard")


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


def process_react_cli_input(
    user_input: str,
    last_react: dict[str, Any] | None,
) -> CLICommandResult:
    """Обрабатывает REPL-команду для режима ReAct."""
    normalized = user_input.strip()
    if not normalized:
        return CLICommandResult(action="noop")
    command = normalized.lower()
    if command == "exit":
        return CLICommandResult(action="exit")
    if command == "clear":
        return CLICommandResult(action="clear")
    if command == "debug":
        return CLICommandResult(action="debug", message=format_react_debug_payload(last_react))
    return CLICommandResult(action="query", message=normalized)


@app.command()
def chat(
    answer_depth: Annotated[
        str | None,
        typer.Option(
            "--answer-depth",
            help="Глубина суммаризации: auto (эвристика), compact или standard.",
        ),
    ] = None,
) -> None:
    """Запускает REPL-чат для работы с оркестратором."""
    try:
        session_overrides = _answer_depth_overrides(answer_depth)
    except typer.BadParameter as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

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
            state = execute_query_sync(
                command_result.message or "",
                state_overrides=session_overrides,
            )
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


@app.command("react")
def react_e2e(
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            "-q",
            help="Один запрос к ReAct-агенту и выход (без REPL).",
        ),
    ] = None,
    recursion_limit: Annotated[
        int | None,
        typer.Option(
            "--recursion-limit",
            help="Лимит шагов LangGraph (по умолчанию из MAX_RECURSION в settings).",
        ),
    ] = None,
) -> None:
    """Интерактивный или одноразовый запуск ReAct-агента (те же MCP-инструменты), для e2e-сравнения."""
    if query is not None:
        stripped = query.strip()
        if not stripped:
            typer.echo("Пустой запрос: укажите текст после -q или запустите без -q для REPL.")
            raise typer.Exit(code=1)
        try:
            result = execute_react_query_sync(stripped, recursion_limit=recursion_limit)
            typer.echo(format_react_cli_output(result))
        except RuntimeError as error:
            typer.echo(str(error))
            raise typer.Exit(code=1) from error
        except Exception as error:
            log.error("cli_react_failed", error=str(error))
            typer.echo(f"Ошибка ReAct: {error}")
            raise typer.Exit(code=1) from error
        return

    typer.echo("ReAct e2e (LangGraph + MCP). Команды: exit, clear, debug.")
    last_react: dict[str, Any] | None = None

    while True:
        user_input = typer.prompt("react>>>")
        command_result = process_react_cli_input(user_input, last_react)

        if command_result.action == "noop":
            continue
        if command_result.action == "exit":
            typer.echo("Завершение сессии ReAct.")
            break
        if command_result.action == "clear":
            last_react = None
            typer.clear()
            typer.echo("Контекст ReAct очищен.")
            continue
        if command_result.action == "debug":
            typer.echo(command_result.message or "")
            continue

        try:
            result = execute_react_query_sync(
                command_result.message or "",
                recursion_limit=recursion_limit,
            )
            last_react = result
            typer.echo(format_react_cli_output(result))
        except RuntimeError as error:
            typer.echo(str(error))
        except Exception as error:
            log.error("cli_react_failed", error=str(error))
            typer.echo(f"Ошибка ReAct: {error}")


@app.command("benchmark-models")
def benchmark_models(
    output_path: Annotated[
        str,
        typer.Option(
            "--output-path",
            help="Куда писать подробные JSONL-записи benchmark.",
        ),
    ] = "data/fixtures/benchmark_metrics.jsonl",
    summary_path: Annotated[
        str | None,
        typer.Option(
            "--summary-path",
            help="Опционально: путь для JSON summary отчёта.",
        ),
    ] = None,
    system: Annotated[
        str,
        typer.Option(
            "--system",
            help="Какие системы запускать: both | orchestrator | react.",
        ),
    ] = "both",
    model: Annotated[
        list[str] | None,
        typer.Option(
            "--model",
            help="Явный список моделей; можно передавать опцию несколько раз.",
        ),
    ] = None,
    limit_models: Annotated[
        int | None,
        typer.Option("--limit-models", help="Ограничить число моделей из списка."),
    ] = None,
    limit_scenarios: Annotated[
        int | None,
        typer.Option("--limit-scenarios", help="Ограничить число сценариев из каталога."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Только показать матрицу запуска без выполнения."),
    ] = False,
) -> None:
    """Массовый прогон model x scenario x system с метриками latency/tools/errors/tokens."""
    normalized_system = system.strip().lower()
    if normalized_system not in {"both", "orchestrator", "react"}:
        typer.echo("Некорректный --system. Ожидается both, orchestrator или react.")
        raise typer.Exit(code=1)
    systems = (
        ["orchestrator", "react"]
        if normalized_system == "both"
        else [normalized_system]
    )

    model_list = list(model or DEFAULT_BENCHMARK_MODELS)
    if limit_models is not None and limit_models >= 0:
        model_list = model_list[:limit_models]
    if not model_list:
        typer.echo("Список моделей пуст. Передайте --model или уберите ограничение.")
        raise typer.Exit(code=1)

    scenarios = list(DEFAULT_BENCHMARK_SCENARIOS)
    if limit_scenarios is not None and limit_scenarios >= 0:
        scenarios = scenarios[:limit_scenarios]
    if not scenarios:
        typer.echo("Список сценариев пуст.")
        raise typer.Exit(code=1)

    total_runs = len(model_list) * len(scenarios) * len(systems)
    typer.echo(
        f"Benchmark plan: models={len(model_list)}, scenarios={len(scenarios)}, "
        f"systems={len(systems)}, runs={total_runs}"
    )
    if dry_run:
        typer.echo("Dry-run завершён: запуск не выполнялся.")
        return

    try:
        asyncio.run(
            run_benchmark_matrix(
                models=model_list,
                scenarios=scenarios,
                systems=systems,
                output_path=output_path,
            )
        )
        summary = build_benchmark_summary_from_file(output_path)
        typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary_path:
            destination = Path(summary_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            typer.echo(f"Summary written: {summary_path}")
    except Exception as error:
        log.error("cli_benchmark_failed", error=str(error))
        typer.echo(f"Ошибка benchmark: {error}")
        raise typer.Exit(code=1) from error


if __name__ == "__main__":
    app()

