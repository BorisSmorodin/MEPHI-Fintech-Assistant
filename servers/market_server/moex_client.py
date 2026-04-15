"""Клиент для работы с MOEX ISS API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any, Callable

import apimoex
import requests
import structlog

from config.settings import Settings, get_settings

log = structlog.get_logger()

ALLOWED_BOARDS = {"TQBR", "TQCB", "TQOB"}
ALLOWED_INTERVALS = {1, 10, 60, 24, 7}
ALLOWED_INDEXES = {"IMOEX", "RTSI", "RGBI"}


class MarketDataError(Exception):
    """Ошибка получения данных с MOEX."""


class TickerNotFoundError(MarketDataError):
    """Тикер не найден в требуемом рынке."""


@dataclass(slots=True)
class TTLCache:
    """Простой in-memory TTL кэш."""

    time_provider: Callable[[], float] = time.time
    _storage: dict[str, tuple[float, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        """Возвращает значение из кэша, если оно не истекло."""
        now = self.time_provider()
        if key not in self._storage:
            return None
        expires_at, value = self._storage[key]
        if now >= expires_at:
            del self._storage[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Сохраняет значение в кэш."""
        expires_at = self.time_provider() + ttl_seconds
        self._storage[key] = (expires_at, value)


@dataclass(slots=True)
class MoexClient:
    """Клиент доступа к MOEX ISS с валидациями и кэшированием."""

    settings: Settings
    session: requests.Session = field(default_factory=requests.Session)
    time_provider: Callable[[], float] = time.time
    _cache: TTLCache = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Инициализирует кэш клиента."""
        self._cache = TTLCache(time_provider=self.time_provider)

    def _cache_get(self, key: str) -> Any | None:
        value = self._cache.get(key)
        if value is None:
            log.info("moex_cache_miss", key=key)
            return None
        log.info("moex_cache_hit", key=key)
        return value

    def _cache_set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._cache.set(key=key, value=value, ttl_seconds=ttl_seconds)

    @staticmethod
    def _validate_ticker(ticker: str) -> str:
        normalized = ticker.strip().upper()
        if not normalized:
            raise MarketDataError("Тикер не может быть пустым.")
        return normalized

    @staticmethod
    def _validate_board(board: str) -> str:
        normalized = board.strip().upper()
        if normalized not in ALLOWED_BOARDS:
            raise MarketDataError(f"Недопустимый board: {board}. Разрешено: {sorted(ALLOWED_BOARDS)}")
        return normalized

    @staticmethod
    def _validate_index(index: str) -> str:
        normalized = index.strip().upper()
        if normalized not in ALLOWED_INDEXES:
            raise MarketDataError(f"Недопустимый индекс: {index}. Разрешено: {sorted(ALLOWED_INDEXES)}")
        return normalized

    @staticmethod
    def _validate_date(date_text: str, field_name: str) -> datetime:
        try:
            return datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError as error:
            raise MarketDataError(f"{field_name} должен быть в формате YYYY-MM-DD.") from error

    def _board_market(self, board: str) -> str:
        return "shares" if board == "TQBR" else "bonds"

    def _request_json(self, url: str) -> dict[str, Any]:
        """Выполняет HTTP-запрос c таймаутом и возвращает JSON."""
        response = self.session.get(url, timeout=self.settings.market_server_timeout)
        response.raise_for_status()
        return response.json()

    def get_stock_quote(self, ticker: str) -> dict[str, Any]:
        """Возвращает котировку акции TQBR."""
        normalized_ticker = self._validate_ticker(ticker)
        cache_key = f"stock_quote:{normalized_ticker}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            rows = apimoex.get_board_securities(
                self.session,
                board="TQBR",
                market="shares",
                columns=("SECID", "LAST", "CHANGE", "VOLTODAY", "BID", "OFFER", "UPDATETIME"),
            )
        except Exception as error:
            raise MarketDataError(f"Ошибка запроса котировок MOEX: {error}") from error

        for row in rows:
            if str(row.get("SECID", "")).upper() == normalized_ticker:
                quote = {
                    "SECID": normalized_ticker,
                    "LAST": float(row.get("LAST") or 0.0),
                    "CHANGE": float(row.get("CHANGE") or 0.0),
                    "VOLTODAY": float(row.get("VOLTODAY") or 0.0),
                    "BID": float(row.get("BID") or 0.0),
                    "OFFER": float(row.get("OFFER") or 0.0),
                    "UPDATETIME": str(row.get("UPDATETIME") or ""),
                }
                self._cache_set(cache_key, quote, self.settings.market_quote_cache_ttl)
                return quote
        raise TickerNotFoundError(f"Тикер {normalized_ticker} не найден на TQBR")

    def get_candles(self, ticker: str, date_from: str, date_to: str, interval: int = 24) -> list[dict]:
        """Возвращает свечи OHLCV за период."""
        normalized_ticker = self._validate_ticker(ticker)
        if interval not in ALLOWED_INTERVALS:
            raise MarketDataError(f"Недопустимый interval: {interval}. Разрешено: {sorted(ALLOWED_INTERVALS)}")

        start_dt = self._validate_date(date_from, "date_from")
        end_dt = self._validate_date(date_to, "date_to")
        if end_dt < start_dt:
            raise MarketDataError("date_to не может быть меньше date_from.")
        if (end_dt - start_dt).days > 1825:
            raise MarketDataError("Максимальный период для свечей составляет 1825 дней.")

        cache_key = f"candles:{normalized_ticker}:{date_from}:{date_to}:{interval}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            rows = apimoex.get_market_candles(
                self.session,
                security=normalized_ticker,
                interval=interval,
                start=date_from,
                end=date_to,
                columns=("begin", "open", "high", "low", "close", "volume"),
                market="shares",
                engine="stock",
            )
        except Exception as error:
            raise MarketDataError(f"Ошибка запроса свечей MOEX: {error}") from error

        candles = [
            {
                "begin": str(row.get("begin")),
                "open": float(row.get("open") or 0.0),
                "high": float(row.get("high") or 0.0),
                "low": float(row.get("low") or 0.0),
                "close": float(row.get("close") or 0.0),
                "volume": float(row.get("volume") or 0.0),
            }
            for row in rows
        ]
        self._cache_set(cache_key, candles, self.settings.market_history_cache_ttl)
        return candles

    def get_board_securities(self, board: str = "TQBR") -> list[dict]:
        """Возвращает список бумаг на указанной доске."""
        normalized_board = self._validate_board(board)
        cache_key = f"board_securities:{normalized_board}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            rows = apimoex.get_board_securities(
                self.session,
                board=normalized_board,
                market=self._board_market(normalized_board),
                columns=("SECID", "SHORTNAME", "LOTSIZE", "PREVPRICE"),
            )
        except Exception as error:
            raise MarketDataError(f"Ошибка запроса списка бумаг MOEX: {error}") from error

        payload = [
            {
                "SECID": str(row.get("SECID") or ""),
                "SHORTNAME": str(row.get("SHORTNAME") or ""),
                "LOTSIZE": int(row.get("LOTSIZE") or 0),
                "PREVPRICE": float(row.get("PREVPRICE") or 0.0),
            }
            for row in rows
        ]
        self._cache_set(cache_key, payload, self.settings.market_history_cache_ttl)
        return payload

    def get_index_analytics(self, index: str = "IMOEX") -> dict[str, Any]:
        """Возвращает значение, изменение и состав индекса."""
        normalized_index = self._validate_index(index)
        cache_key = f"index_analytics:{normalized_index}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            tickers = apimoex.get_index_tickers(
                self.session,
                index=normalized_index,
                columns=("ticker", "weight"),
                market="index",
                engine="stock",
            )
            url = (
                "https://iss.moex.com/iss/engines/stock/markets/index/securities/"
                f"{normalized_index}.json"
            )
            raw_payload = self._request_json(url)
        except Exception as error:
            raise MarketDataError(f"Ошибка запроса аналитики индекса MOEX: {error}") from error

        market_data = raw_payload.get("marketdata", {})
        columns = market_data.get("columns", [])
        data = market_data.get("data", [])
        value = 0.0
        change = 0.0
        if columns and data:
            row = data[0]
            row_map = {columns[idx]: row[idx] for idx in range(min(len(columns), len(row)))}
            value = float(row_map.get("LASTVALUE") or row_map.get("CURRENTVALUE") or 0.0)
            change = float(row_map.get("CHANGE") or 0.0)

        components = [
            {"ticker": str(row.get("ticker") or ""), "weight": float(row.get("weight") or 0.0)}
            for row in tickers
        ]
        payload = {"value": value, "change": change, "components": components}
        self._cache_set(cache_key, payload, self.settings.market_history_cache_ttl)
        return payload

    def get_bond_data(self, ticker: str) -> dict[str, Any]:
        """Возвращает ключевые параметры облигации из досок TQCB/TQOB."""
        normalized_ticker = self._validate_ticker(ticker)
        cache_key = f"bond_data:{normalized_ticker}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        for board in ("TQCB", "TQOB"):
            url = (
                "https://iss.moex.com/iss/engines/stock/markets/bonds/boards/"
                f"{board}/securities/{normalized_ticker}.json"
            )
            try:
                raw_payload = self._request_json(url)
            except requests.HTTPError:
                continue
            except Exception as error:
                raise MarketDataError(f"Ошибка запроса данных облигации MOEX: {error}") from error

            securities = raw_payload.get("securities", {})
            securities_columns = securities.get("columns", [])
            securities_data = securities.get("data", [])
            market = raw_payload.get("marketdata", {})
            market_columns = market.get("columns", [])
            market_data = market.get("data", [])

            sec_map: dict[str, Any] = {}
            if securities_columns and securities_data:
                sec_row = securities_data[0]
                sec_map = {
                    securities_columns[idx]: sec_row[idx]
                    for idx in range(min(len(securities_columns), len(sec_row)))
                }
            market_map: dict[str, Any] = {}
            if market_columns and market_data:
                market_row = market_data[0]
                market_map = {
                    market_columns[idx]: market_row[idx]
                    for idx in range(min(len(market_columns), len(market_row)))
                }

            if not sec_map and not market_map:
                continue

            payload = {
                "SECID": normalized_ticker,
                "FACEVALUE": float(sec_map.get("FACEVALUE") or 0.0),
                "COUPONVALUE": float(sec_map.get("COUPONVALUE") or 0.0),
                "ACCINT": float(market_map.get("ACCINT") or sec_map.get("ACCINT") or 0.0),
                "YIELDATPREVWAPRICE": float(market_map.get("YIELDATPREVWAPRICE") or 0.0),
                "DURATION": float(market_map.get("DURATION") or sec_map.get("DURATION") or 0.0),
                "MATDATE": str(sec_map.get("MATDATE") or ""),
            }
            self._cache_set(cache_key, payload, self.settings.market_history_cache_ttl)
            return payload

        raise TickerNotFoundError(f"Облигация {normalized_ticker} не найдена на TQCB/TQOB")


def get_moex_client(
    settings: Settings | None = None,
    session: requests.Session | None = None,
    time_provider: Callable[[], float] | None = None,
) -> MoexClient:
    """Возвращает клиент MOEX c настройками по умолчанию."""
    current_settings = settings or get_settings()
    return MoexClient(
        settings=current_settings,
        session=session or requests.Session(),
        time_provider=time_provider or time.time,
    )

