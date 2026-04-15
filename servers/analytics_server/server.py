"""Точка входа MCP-сервера аналитики."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
import sqlglot
from sqlglot import exp
import structlog

from config.settings import get_settings
from servers.analytics_server.clickhouse_client import (
    AnalyticsDataClient,
    get_analytics_data_client,
)
from servers.analytics_server.risk_calculator import calculate_risk_metrics as calculate_risk_metrics_core
from servers.analytics_server.stress_tester import run_stress_test as run_stress_test_core

log = structlog.get_logger()
settings = get_settings()
mcp = FastMCP("analytics_server")

FORBIDDEN_SQL_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
)
FORBIDDEN_SQL_EXPRESSION_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
)


def _get_client() -> AnalyticsDataClient:
    """Возвращает сконфигурированный клиент данных аналитики."""
    return get_analytics_data_client(settings)


def _normalize_positions(raw_positions: list[dict]) -> list[dict]:
    """Приводит типы полей позиций к расчетному формату."""
    positions: list[dict] = []
    for row in raw_positions:
        positions.append(
            {
                "portfolio_id": str(row["portfolio_id"]),
                "ticker": str(row["ticker"]),
                "quantity": float(row["quantity"]),
                "avg_price": float(row["avg_price"]),
                "sector": str(row["sector"]),
                "instrument_type": str(row["instrument_type"]),
                "currency": str(row["currency"]),
            }
        )
    return positions


def _build_portfolio_summary(portfolio_id: str, client: AnalyticsDataClient) -> dict[str, Any]:
    """Собирает сводку портфеля согласно контракту ТЗ."""
    raw_positions = client.get_portfolio_positions(portfolio_id)
    positions = _normalize_positions(raw_positions)
    if not positions:
        raise ToolError(f"Портфель {portfolio_id} не найден.")

    tickers = [position["ticker"] for position in positions]
    current_prices = client.get_latest_prices(tickers)

    total_value = 0.0
    total_cost = 0.0
    position_rows: list[dict[str, Any]] = []
    by_sector: dict[str, float] = defaultdict(float)
    by_type: dict[str, float] = defaultdict(float)
    by_currency: dict[str, float] = defaultdict(float)

    for position in positions:
        ticker = position["ticker"]
        current_price = float(current_prices.get(ticker, 0.0))
        market_value = position["quantity"] * current_price
        invested_value = position["quantity"] * position["avg_price"]
        pnl = market_value - invested_value
        pnl_pct = pnl / invested_value if invested_value else 0.0

        total_value += market_value
        total_cost += invested_value
        by_sector[position["sector"]] += market_value
        by_type[position["instrument_type"]] += market_value
        by_currency[position["currency"]] += market_value

        position_rows.append(
            {
                "ticker": ticker,
                "quantity": round(position["quantity"], 4),
                "avg_price": round(position["avg_price"], 4),
                "current_price": round(current_price, 4),
                "market_value": round(market_value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 6),
                "weight": 0.0,
                "sector": position["sector"],
                "instrument_type": position["instrument_type"],
            }
        )

    for row in position_rows:
        row["weight"] = round(row["market_value"] / total_value, 6) if total_value else 0.0

    total_pnl = total_value - total_cost
    total_pnl_pct = total_pnl / total_cost if total_cost else 0.0

    return {
        "portfolio_id": portfolio_id,
        "total_value": round(total_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 6),
        "positions": position_rows,
        "allocation": {
            "by_sector": {sector: round(value, 2) for sector, value in by_sector.items()},
            "by_type": {kind: round(value, 2) for kind, value in by_type.items()},
            "by_currency": {currency: round(value, 2) for currency, value in by_currency.items()},
        },
    }


def _calculate_risk_metrics(portfolio_id: str, confidence: float, client: AnalyticsDataClient) -> dict[str, Any]:
    """Рассчитывает метрики риска портфеля."""
    positions = _normalize_positions(client.get_portfolio_positions(portfolio_id))
    if not positions:
        raise ToolError(f"Портфель {portfolio_id} не найден.")

    tickers = [position["ticker"] for position in positions]
    current_prices = client.get_latest_prices(tickers)
    price_rows = client.get_price_history(tickers=tickers)
    if len(price_rows) < 252:
        raise ToolError("Недостаточно исторических данных для риск-расчетов (требуется >= 252 наблюдений).")

    return calculate_risk_metrics_core(
        positions=positions,
        current_prices=current_prices,
        price_rows=price_rows,
        confidence=confidence,
    )


def _run_stress_test(
    portfolio_id: str,
    scenario: str,
    magnitude: float,
    target_sector: str | None,
    client: AnalyticsDataClient,
) -> dict[str, Any]:
    """Запускает стресс-тестирование портфеля по выбранному сценарию."""
    positions = _normalize_positions(client.get_portfolio_positions(portfolio_id))
    if not positions:
        raise ToolError(f"Портфель {portfolio_id} не найден.")

    tickers = [position["ticker"] for position in positions]
    current_prices = client.get_latest_prices(tickers)
    price_rows = client.get_price_history(tickers=tickers)
    bond_tickers = [position["ticker"] for position in positions if position["instrument_type"] == "облигация"]
    bond_details = client.get_bond_details(bond_tickers)

    return run_stress_test_core(
        positions=positions,
        current_prices=current_prices,
        price_rows=price_rows,
        scenario=scenario,
        magnitude=magnitude,
        target_sector=target_sector,
        bond_details=bond_details,
    )


def _validate_and_rewrite_select_query(query: str) -> str:
    """Проверяет SQL-запрос и добавляет LIMIT 1000 при необходимости."""
    normalized = query.upper()
    for keyword in FORBIDDEN_SQL_KEYWORDS:
        if keyword in normalized:
            raise ToolError("Разрешены только SELECT-запросы.")

    try:
        statements = sqlglot.parse(query, read="clickhouse")
    except Exception as error:
        log.warning("invalid_sql_query", error=str(error))
        raise ToolError("Некорректный SQL-запрос.") from error

    if len(statements) != 1:
        raise ToolError("Разрешен только один SELECT-запрос.")

    expression = statements[0]
    if not isinstance(expression, exp.Select):
        raise ToolError("Разрешены только SELECT-запросы.")

    for node in expression.walk():
        if isinstance(node, FORBIDDEN_SQL_EXPRESSION_TYPES):
            raise ToolError("Разрешены только SELECT-запросы.")

    if expression.args.get("limit") is None:
        expression = expression.limit(1000)
    return expression.sql(dialect="clickhouse")


def _execute_analytics_query(query: str, client: AnalyticsDataClient) -> dict[str, Any]:
    """Выполняет безопасный read-only SQL-запрос к аналитическому хранилищу."""
    safe_query = _validate_and_rewrite_select_query(query)
    return client.execute_select(safe_query)


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_portfolio_summary(portfolio_id: str) -> dict[str, Any]:
    """Возвращает полную сводку портфеля: стоимость, PnL, веса и аллокацию."""
    log.info("mcp_tool_called", tool="get_portfolio_summary", portfolio_id=portfolio_id)
    client = _get_client()
    try:
        return _build_portfolio_summary(portfolio_id=portfolio_id, client=client)
    except ToolError:
        raise
    except Exception as error:
        log.error("portfolio_summary_failed", error=str(error), portfolio_id=portfolio_id)
        raise ToolError(f"Ошибка расчета сводки портфеля: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def calculate_risk_metrics(portfolio_id: str, confidence: float = 0.95) -> dict[str, Any]:
    """Рассчитывает VaR, CVaR, волатильность, Sharpe, Max Drawdown и HHI."""
    log.info(
        "mcp_tool_called",
        tool="calculate_risk_metrics",
        portfolio_id=portfolio_id,
        confidence=confidence,
    )
    client = _get_client()
    try:
        return _calculate_risk_metrics(portfolio_id=portfolio_id, confidence=confidence, client=client)
    except ToolError:
        raise
    except Exception as error:
        log.error("risk_metrics_failed", error=str(error), portfolio_id=portfolio_id)
        raise ToolError(f"Ошибка расчета риск-метрик: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def run_stress_test(
    portfolio_id: str,
    scenario: str,
    magnitude: float,
    target_sector: str | None = None,
) -> dict[str, Any]:
    """Проводит стресс-тест по сценариям index_drop, rate_hike, sector_decline."""
    log.info(
        "mcp_tool_called",
        tool="run_stress_test",
        portfolio_id=portfolio_id,
        scenario=scenario,
        magnitude=magnitude,
        target_sector=target_sector,
    )
    client = _get_client()
    try:
        return _run_stress_test(
            portfolio_id=portfolio_id,
            scenario=scenario,
            magnitude=magnitude,
            target_sector=target_sector,
            client=client,
        )
    except ToolError:
        raise
    except Exception as error:
        log.error("stress_test_failed", error=str(error), portfolio_id=portfolio_id, scenario=scenario)
        raise ToolError(f"Ошибка стресс-тестирования: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def execute_analytics_query(query: str) -> dict[str, Any]:
    """Выполняет безопасный SELECT-запрос к ClickHouse (read-only, LIMIT <= 1000)."""
    log.info("mcp_tool_called", tool="execute_analytics_query")
    client = _get_client()
    try:
        return _execute_analytics_query(query=query, client=client)
    except ToolError:
        raise
    except Exception as error:
        log.error("execute_analytics_query_failed", error=str(error))
        raise ToolError(f"Ошибка выполнения аналитического запроса: {error}") from error


if __name__ == "__main__":
    mcp.run()

