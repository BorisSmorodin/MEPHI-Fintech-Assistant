"""Клиент доступа к данным аналитики в real/mock режимах."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import random
from typing import Protocol

import clickhouse_connect
import structlog

from config.settings import Settings, get_settings

log = structlog.get_logger()


class AnalyticsDataClient(Protocol):
    """Контракт слоя доступа к данным аналитики."""

    def get_portfolio_positions(self, portfolio_id: str) -> list[dict]:
        """Возвращает позиции портфеля."""

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """Возвращает последние цены по набору тикеров."""

    def get_price_history(
        self,
        tickers: list[str],
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        """Возвращает исторические цены по тикерам за указанный диапазон."""

    def get_bond_details(self, tickers: list[str]) -> dict[str, dict]:
        """Возвращает словарь параметров облигаций по тикерам."""

    def execute_select(self, query: str) -> dict[str, object]:
        """Выполняет read-only SELECT и возвращает табличный результат."""


@dataclass(slots=True)
class ClickHouseClient:
    """Read-only клиент ClickHouse для real-режима."""

    settings: Settings
    _client: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Инициализирует read-only подключение к ClickHouse."""
        self._client = clickhouse_connect.get_client(
            host=self.settings.clickhouse_host,
            port=self.settings.clickhouse_port,
            database=self.settings.clickhouse_database,
            username=self.settings.clickhouse_user,
            password=self.settings.clickhouse_password,
            settings={"readonly": 1},
        )

    def get_portfolio_positions(self, portfolio_id: str) -> list[dict]:
        """Возвращает позиции портфеля из таблицы portfolios."""
        query = """
            SELECT portfolio_id, ticker, quantity, avg_price, sector, instrument_type, currency
            FROM portfolios
            WHERE portfolio_id = %(portfolio_id)s
        """
        log.info("clickhouse_query", query_name="get_portfolio_positions", portfolio_id=portfolio_id)
        result = self._client.query(
            query,
            parameters={"portfolio_id": portfolio_id},
            settings={"max_execution_time": self.settings.clickhouse_query_timeout_sec},
        )
        columns = list(result.column_names)
        return [dict(zip(columns, row, strict=False)) for row in result.result_rows]

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """Возвращает последние цены закрытия по каждому тикеру."""
        if not tickers:
            return {}

        query = """
            SELECT ticker, argMax(close, date) AS last_close
            FROM price_history
            WHERE ticker IN %(tickers)s
            GROUP BY ticker
        """
        log.info("clickhouse_query", query_name="get_latest_prices", tickers_count=len(tickers))
        result = self._client.query(
            query,
            parameters={"tickers": tuple(tickers)},
            settings={"max_execution_time": self.settings.clickhouse_query_timeout_sec},
        )
        return {row[0]: float(row[1]) for row in result.result_rows}

    def get_price_history(
        self,
        tickers: list[str],
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        """Возвращает исторические свечи по тикерам."""
        if not tickers:
            return []

        filters: list[str] = ["ticker IN %(tickers)s"]
        parameters: dict[str, object] = {"tickers": tuple(tickers)}
        if date_from is not None:
            filters.append("date >= %(date_from)s")
            parameters["date_from"] = date_from
        if date_to is not None:
            filters.append("date <= %(date_to)s")
            parameters["date_to"] = date_to

        query = f"""
            SELECT ticker, date, open, high, low, close, volume
            FROM price_history
            WHERE {' AND '.join(filters)}
            ORDER BY ticker, date
        """
        log.info("clickhouse_query", query_name="get_price_history", tickers_count=len(tickers))
        result = self._client.query(
            query,
            parameters=parameters,
            settings={"max_execution_time": self.settings.clickhouse_query_timeout_sec},
        )
        columns = list(result.column_names)
        return [dict(zip(columns, row, strict=False)) for row in result.result_rows]

    def get_bond_details(self, tickers: list[str]) -> dict[str, dict]:
        """Возвращает параметры облигаций из таблицы bond_details."""
        if not tickers:
            return {}

        query = """
            SELECT ticker, duration, coupon_rate, ytm, maturity_date
            FROM bond_details
            WHERE ticker IN %(tickers)s
        """
        log.info("clickhouse_query", query_name="get_bond_details", tickers_count=len(tickers))
        result = self._client.query(
            query,
            parameters={"tickers": tuple(tickers)},
            settings={"max_execution_time": self.settings.clickhouse_query_timeout_sec},
        )
        return {
            str(row[0]): {
                "duration": float(row[1]),
                "coupon_rate": float(row[2]),
                "ytm": float(row[3]),
                "maturity_date": str(row[4]),
            }
            for row in result.result_rows
        }

    def execute_select(self, query: str) -> dict[str, object]:
        """Выполняет read-only SELECT запрос к ClickHouse."""
        log.info("clickhouse_query", query_name="execute_select")
        result = self._client.query(
            query,
            settings={"max_execution_time": self.settings.clickhouse_query_timeout_sec},
        )
        return {
            "columns": list(result.column_names),
            "rows": [list(row) for row in result.result_rows],
            "row_count": len(result.result_rows),
        }


@dataclass(slots=True)
class MockClickHouseClient:
    """Mock-реализация слоя данных на JSON-фикстурах."""

    settings: Settings
    fixtures_dir: Path | None = None
    _fixtures_dir: Path = field(init=False, repr=False)
    _portfolio_rows: list[dict] = field(init=False, repr=False)
    _candles_config: dict = field(init=False, repr=False)
    _price_rows: list[dict] = field(init=False, repr=False)
    _bond_details: dict[str, dict] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Загружает fixture-данные и готовит кэш исторических рядов."""
        project_root = Path(__file__).resolve().parents[2]
        self._fixtures_dir = self.fixtures_dir or (project_root / "data" / "fixtures")
        self._portfolio_rows = self._load_json("portfolio_sample.json")
        self._candles_config = self._load_json("moex_candles_sample.json")
        self._price_rows = self._generate_price_history()
        self._bond_details = self._build_mock_bond_details()

    def _load_json(self, filename: str) -> list[dict] | dict:
        """Загружает JSON-файл из директории fixtures."""
        path = self._fixtures_dir / filename
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _generate_price_history(self) -> list[dict]:
        """Генерирует детерминированные дневные свечи на основе параметров fixtures."""
        metadata = self._candles_config["metadata"]
        start_date = datetime.strptime(metadata["start_date"], "%Y-%m-%d").date()
        trading_days = int(metadata["trading_days"])

        rows: list[dict] = []
        for ticker_config in self._candles_config["tickers"]:
            ticker = str(ticker_config["ticker"])
            base_price = float(ticker_config["base_price"])
            daily_drift = float(ticker_config["daily_drift"])
            daily_volatility = float(ticker_config["daily_volatility"])
            base_volume = float(ticker_config["base_volume"])

            seed_value = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:16], 16)
            random_state = random.Random(seed_value)
            current_date = start_date
            close_price = base_price
            generated = 0

            while generated < trading_days:
                if current_date.weekday() >= 5:
                    current_date += timedelta(days=1)
                    continue

                open_price = close_price
                daily_noise = random_state.gauss(0.0, daily_volatility)
                close_price = max(1e-2, open_price * (1.0 + daily_drift + daily_noise))
                intraday_spread = max(
                    1e-2,
                    abs(random_state.gauss(0.0, daily_volatility / 2.0)) * open_price,
                )
                high_price = max(open_price, close_price) + intraday_spread
                low_price = max(1e-2, min(open_price, close_price) - intraday_spread)
                volume = max(1.0, base_volume * (1.0 + random_state.gauss(0.0, 0.12)))

                rows.append(
                    {
                        "ticker": ticker,
                        "date": current_date.isoformat(),
                        "open": round(open_price, 4),
                        "high": round(high_price, 4),
                        "low": round(low_price, 4),
                        "close": round(close_price, 4),
                        "volume": round(volume, 4),
                    }
                )

                generated += 1
                current_date += timedelta(days=1)

        return rows

    def _build_mock_bond_details(self) -> dict[str, dict]:
        """Формирует mock-параметры облигаций для стресс-тестов."""
        return {
            "OFZ26243": {
                "duration": 5.4,
                "coupon_rate": 11.6,
                "ytm": 12.0,
                "maturity_date": "2030-11-15",
            },
            "OFZ29024": {
                "duration": 4.9,
                "coupon_rate": 10.4,
                "ytm": 11.1,
                "maturity_date": "2029-04-24",
            },
            "RU000A105ZX4": {
                "duration": 2.8,
                "coupon_rate": 13.3,
                "ytm": 13.9,
                "maturity_date": "2028-10-01",
            },
            "RU000A1061Q0": {
                "duration": 3.4,
                "coupon_rate": 12.7,
                "ytm": 13.2,
                "maturity_date": "2029-07-20",
            },
        }

    def get_portfolio_positions(self, portfolio_id: str) -> list[dict]:
        """Возвращает позиции конкретного портфеля из fixture."""
        return [
            row
            for row in self._portfolio_rows
            if str(row["portfolio_id"]).strip() == portfolio_id.strip()
        ]

    def get_latest_prices(self, tickers: list[str]) -> dict[str, float]:
        """Возвращает latest close по каждому тикеру из mock history."""
        history = self.get_price_history(tickers=tickers)
        latest: dict[str, tuple[str, float]] = {}
        for row in history:
            ticker = str(row["ticker"])
            row_date = str(row["date"])
            row_close = float(row["close"])
            if ticker not in latest or row_date > latest[ticker][0]:
                latest[ticker] = (row_date, row_close)
        return {ticker: value[1] for ticker, value in latest.items()}

    def get_price_history(
        self,
        tickers: list[str],
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        """Возвращает исторические данные по фильтрам тикеров и дат."""
        ticker_set = set(tickers)
        rows = [row for row in self._price_rows if row["ticker"] in ticker_set]
        if date_from is not None:
            min_date = date_from.isoformat()
            rows = [row for row in rows if row["date"] >= min_date]
        if date_to is not None:
            max_date = date_to.isoformat()
            rows = [row for row in rows if row["date"] <= max_date]
        return rows

    def get_bond_details(self, tickers: list[str]) -> dict[str, dict]:
        """Возвращает bond_details из подготовленного mock-набора."""
        return {ticker: self._bond_details[ticker] for ticker in tickers if ticker in self._bond_details}

    def execute_select(self, query: str) -> dict[str, object]:
        """В mock-режиме поддерживает только простые SELECT по fixture-таблицам."""
        normalized = query.strip().lower()
        limit = 1000
        if "limit" in normalized:
            try:
                limit = int(normalized.rsplit("limit", maxsplit=1)[1].strip().split()[0])
            except (IndexError, ValueError) as error:
                raise ValueError("Некорректный LIMIT в mock SELECT запросе.") from error

        if "from portfolios" in normalized:
            rows = self._portfolio_rows[:limit]
            columns = [
                "portfolio_id",
                "ticker",
                "quantity",
                "avg_price",
                "sector",
                "instrument_type",
                "currency",
            ]
            data_rows = [[row[column] for column in columns] for row in rows]
            return {"columns": columns, "rows": data_rows, "row_count": len(data_rows)}

        if "from price_history" in normalized:
            rows = self._price_rows[:limit]
            columns = ["ticker", "date", "open", "high", "low", "close", "volume"]
            data_rows = [[row[column] for column in columns] for row in rows]
            return {"columns": columns, "rows": data_rows, "row_count": len(data_rows)}

        if "from bond_details" in normalized:
            rows = list(self._bond_details.items())[:limit]
            columns = ["ticker", "duration", "coupon_rate", "ytm", "maturity_date"]
            data_rows = [
                [ticker, details["duration"], details["coupon_rate"], details["ytm"], details["maturity_date"]]
                for ticker, details in rows
            ]
            return {"columns": columns, "rows": data_rows, "row_count": len(data_rows)}

        raise ValueError("MockClickHouseClient поддерживает только SELECT из portfolios/price_history.")


def get_analytics_data_client(settings: Settings | None = None) -> AnalyticsDataClient:
    """Возвращает real или mock клиент в зависимости от конфигурации."""
    current_settings = settings or get_settings()
    if current_settings.use_mock_clickhouse:
        log.info("analytics_data_client_selected", mode="mock")
        return MockClickHouseClient(current_settings)

    log.info("analytics_data_client_selected", mode="real")
    try:
        client = ClickHouseClient(current_settings)
        # Пробный запрос позволяет рано обнаружить недоступность ClickHouse.
        client.execute_select("SELECT 1")
        return client
    except Exception as error:
        if current_settings.allow_mock_fallback_on_clickhouse_error:
            log.warning("analytics_clickhouse_unavailable_fallback_to_mock", error=str(error))
            return MockClickHouseClient(current_settings)
        raise

