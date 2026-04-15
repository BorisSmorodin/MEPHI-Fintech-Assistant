"""Точка входа MCP-сервера рынка."""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
import structlog

from servers.market_server.moex_client import (
    MarketDataError,
    MoexClient,
    TickerNotFoundError,
    get_moex_client,
)

log = structlog.get_logger()
mcp = FastMCP("market_server")
_client: MoexClient | None = None


def _get_client() -> MoexClient:
    """Возвращает singleton клиента MOEX."""
    global _client
    if _client is None:
        _client = get_moex_client()
    return _client


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_stock_quote(ticker: str) -> dict[str, Any]:
    """Возвращает текущую котировку акции с доски TQBR."""
    log.info("mcp_tool_called", tool="get_stock_quote", ticker=ticker)
    try:
        return _get_client().get_stock_quote(ticker)
    except TickerNotFoundError:
        raise ToolError(f"Тикер {ticker.strip().upper()} не найден на TQBR")
    except MarketDataError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("market_tool_failed", tool="get_stock_quote", error=str(error))
        raise ToolError(f"Ошибка получения котировки: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_candles(
    ticker: str,
    date_from: str,
    date_to: str,
    interval: int = 24,
) -> list[dict]:
    """Возвращает OHLCV свечи по тикеру за указанный период."""
    log.info(
        "mcp_tool_called",
        tool="get_candles",
        ticker=ticker,
        date_from=date_from,
        date_to=date_to,
        interval=interval,
    )
    try:
        return _get_client().get_candles(ticker=ticker, date_from=date_from, date_to=date_to, interval=interval)
    except MarketDataError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("market_tool_failed", tool="get_candles", error=str(error))
        raise ToolError(f"Ошибка получения свечей: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_board_securities(board: str = "TQBR") -> list[dict]:
    """Возвращает список бумаг по выбранной торговой доске."""
    log.info("mcp_tool_called", tool="get_board_securities", board=board)
    try:
        return _get_client().get_board_securities(board=board)
    except MarketDataError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("market_tool_failed", tool="get_board_securities", error=str(error))
        raise ToolError(f"Ошибка получения списка бумаг: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_index_analytics(index: str = "IMOEX") -> dict[str, Any]:
    """Возвращает аналитику индекса: текущее значение, изменение и компоненты."""
    log.info("mcp_tool_called", tool="get_index_analytics", index=index)
    try:
        return _get_client().get_index_analytics(index=index)
    except MarketDataError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("market_tool_failed", tool="get_index_analytics", error=str(error))
        raise ToolError(f"Ошибка получения аналитики индекса: {error}") from error


@mcp.tool(
    annotations={"readOnlyHint": True, "idempotentHint": True},
)
async def get_bond_data(ticker: str) -> dict[str, Any]:
    """Возвращает параметры облигации по тикеру из TQCB/TQOB."""
    log.info("mcp_tool_called", tool="get_bond_data", ticker=ticker)
    try:
        return _get_client().get_bond_data(ticker=ticker)
    except TickerNotFoundError as error:
        raise ToolError(str(error)) from error
    except MarketDataError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("market_tool_failed", tool="get_bond_data", error=str(error))
        raise ToolError(f"Ошибка получения данных облигации: {error}") from error


if __name__ == "__main__":
    mcp.run()

