"""Юнит-тесты market_server."""

from __future__ import annotations

from typing import Any

import pytest
import requests

from config.settings import Settings
from servers.market_server.moex_client import (
    MarketDataError,
    MoexClient,
    TickerNotFoundError,
)
from servers.market_server.server import (
    get_board_securities,
    get_bond_data,
    get_candles,
    get_index_analytics,
    get_stock_quote,
)


class DummyResponse:
    """Тестовый HTTP response."""

    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _make_client(monkeypatch, time_provider=lambda: 0.0) -> MoexClient:
    """Создает клиента с тестовыми настройками."""
    settings = Settings(
        market_quote_cache_ttl=60,
        market_history_cache_ttl=300,
        market_server_timeout=30,
    )
    return MoexClient(settings=settings, session=requests.Session(), time_provider=time_provider)


def test_get_stock_quote_success(monkeypatch) -> None:
    """Проверяет успешное получение котировки."""
    client = _make_client(monkeypatch)

    def fake_get_board_securities(*_args, **_kwargs):
        return [
            {"SECID": "SBER", "LAST": 300.5, "CHANGE": 1.2, "VOLTODAY": 10000, "BID": 300.4, "OFFER": 300.6, "UPDATETIME": "12:00:00"}
        ]

    monkeypatch.setattr("servers.market_server.moex_client.apimoex.get_board_securities", fake_get_board_securities)
    result = client.get_stock_quote("sber")
    assert result["SECID"] == "SBER"
    assert result["LAST"] == 300.5


def test_get_stock_quote_ticker_not_found(monkeypatch) -> None:
    """Проверяет ошибку при неизвестном тикере."""
    client = _make_client(monkeypatch)
    monkeypatch.setattr("servers.market_server.moex_client.apimoex.get_board_securities", lambda *_args, **_kwargs: [])
    with pytest.raises(TickerNotFoundError):
        client.get_stock_quote("XXXX")


def test_get_candles_validations(monkeypatch) -> None:
    """Проверяет валидацию дат и интервала."""
    client = _make_client(monkeypatch)
    with pytest.raises(MarketDataError):
        client.get_candles("SBER", "2025-01-10", "2025-01-01", 24)
    with pytest.raises(MarketDataError):
        client.get_candles("SBER", "bad", "2025-01-10", 24)
    with pytest.raises(MarketDataError):
        client.get_candles("SBER", "2020-01-01", "2026-01-01", 24)
    with pytest.raises(MarketDataError):
        client.get_candles("SBER", "2025-01-01", "2025-01-10", 999)


def test_get_board_securities_board_validation(monkeypatch) -> None:
    """Проверяет валидацию board."""
    client = _make_client(monkeypatch)
    with pytest.raises(MarketDataError):
        client.get_board_securities("BAD")


def test_get_index_analytics_index_validation(monkeypatch) -> None:
    """Проверяет валидацию индекса."""
    client = _make_client(monkeypatch)
    with pytest.raises(MarketDataError):
        client.get_index_analytics("BAD")


def test_cache_hit_and_expiration(monkeypatch) -> None:
    """Проверяет cache miss/hit/expiration для котировок."""
    current_time = {"value": 1000.0}

    def time_provider() -> float:
        return current_time["value"]

    client = _make_client(monkeypatch, time_provider=time_provider)
    call_counter = {"count": 0}

    def fake_get_board_securities(*_args, **_kwargs):
        call_counter["count"] += 1
        return [
            {"SECID": "SBER", "LAST": 300.5, "CHANGE": 1.2, "VOLTODAY": 10000, "BID": 300.4, "OFFER": 300.6, "UPDATETIME": "12:00:00"}
        ]

    monkeypatch.setattr("servers.market_server.moex_client.apimoex.get_board_securities", fake_get_board_securities)

    first = client.get_stock_quote("SBER")
    second = client.get_stock_quote("SBER")
    assert first == second
    assert call_counter["count"] == 1

    current_time["value"] += 61.0
    third = client.get_stock_quote("SBER")
    assert third["SECID"] == "SBER"
    assert call_counter["count"] == 2


def test_get_index_analytics_contract(monkeypatch) -> None:
    """Проверяет контракт ответа get_index_analytics."""
    client = _make_client(monkeypatch)
    monkeypatch.setattr(
        "servers.market_server.moex_client.apimoex.get_index_tickers",
        lambda *_args, **_kwargs: [{"ticker": "SBER", "weight": 15.2}],
    )
    monkeypatch.setattr(
        "servers.market_server.moex_client.MoexClient._request_json",
        lambda *_args, **_kwargs: {
            "marketdata": {"columns": ["LASTVALUE", "CHANGE"], "data": [[3200.0, -0.5]]}
        },
    )
    result = client.get_index_analytics("IMOEX")
    assert result["value"] == 3200.0
    assert result["change"] == -0.5
    assert result["components"][0]["ticker"] == "SBER"


def test_get_bond_data_contract(monkeypatch) -> None:
    """Проверяет контракт ответа get_bond_data."""
    client = _make_client(monkeypatch)
    monkeypatch.setattr(
        "servers.market_server.moex_client.MoexClient._request_json",
        lambda *_args, **_kwargs: {
            "securities": {
                "columns": ["FACEVALUE", "COUPONVALUE", "MATDATE", "DURATION"],
                "data": [[1000.0, 45.0, "2030-01-01", 3.2]],
            },
            "marketdata": {
                "columns": ["ACCINT", "YIELDATPREVWAPRICE", "DURATION"],
                "data": [[10.5, 12.1, 3.3]],
            },
        },
    )
    result = client.get_bond_data("OFZ26243")
    assert result["SECID"] == "OFZ26243"
    assert result["FACEVALUE"] == 1000.0
    assert result["YIELDATPREVWAPRICE"] == 12.1


@pytest.mark.asyncio
async def test_market_server_tool_smoke(monkeypatch) -> None:
    """Проверяет smoke-вызов MCP-инструментов market_server."""

    class FakeClient:
        def get_stock_quote(self, ticker: str) -> dict[str, Any]:
            return {"SECID": ticker.upper(), "LAST": 1.0, "CHANGE": 0.0, "VOLTODAY": 0.0, "BID": 0.0, "OFFER": 0.0, "UPDATETIME": "00:00:00"}

        def get_candles(self, *_args, **_kwargs) -> list[dict]:
            return [{"begin": "2025-01-01", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}]

        def get_board_securities(self, *_args, **_kwargs) -> list[dict]:
            return [{"SECID": "SBER", "SHORTNAME": "Сбербанк", "LOTSIZE": 10, "PREVPRICE": 300.0}]

        def get_index_analytics(self, *_args, **_kwargs) -> dict[str, Any]:
            return {"value": 3200.0, "change": -0.5, "components": [{"ticker": "SBER", "weight": 15.0}]}

        def get_bond_data(self, ticker: str) -> dict[str, Any]:
            return {"SECID": ticker, "FACEVALUE": 1000.0, "COUPONVALUE": 45.0, "ACCINT": 10.0, "YIELDATPREVWAPRICE": 12.0, "DURATION": 3.0, "MATDATE": "2030-01-01"}

    monkeypatch.setattr("servers.market_server.server._get_client", lambda: FakeClient())

    quote = await get_stock_quote("SBER")
    assert quote["SECID"] == "SBER"
    candles = await get_candles("SBER", "2025-01-01", "2025-01-10", 24)
    assert len(candles) == 1
    board_rows = await get_board_securities("TQBR")
    assert board_rows[0]["SECID"] == "SBER"
    index_data = await get_index_analytics("IMOEX")
    assert index_data["value"] == 3200.0
    bond = await get_bond_data("OFZ26243")
    assert bond["SECID"] == "OFZ26243"

