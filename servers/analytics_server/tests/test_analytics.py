"""Тесты инфраструктуры analytics_server (Этап 1)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import sqlglot

from config.settings import Settings
from servers.analytics_server.clickhouse_client import (
    ClickHouseClient,
    MockClickHouseClient,
    get_analytics_data_client,
)


def test_ddl_contains_required_tables_and_columns(project_root: Path) -> None:
    """Проверяет, что миграция содержит обязательные таблицы и поля."""
    migration_path = project_root / "data" / "migrations" / "001_initial.sql"
    sql_text = migration_path.read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS portfolios" in sql_text
    assert "CREATE TABLE IF NOT EXISTS price_history" in sql_text
    assert "CREATE TABLE IF NOT EXISTS bond_details" in sql_text
    assert "portfolio_id String" in sql_text
    assert "ticker String" in sql_text
    assert "PARTITION BY toYYYYMM(date)" in sql_text


def test_ddl_has_valid_clickhouse_syntax(project_root: Path) -> None:
    """Проверяет синтаксис DDL через sqlglot."""
    migration_path = project_root / "data" / "migrations" / "001_initial.sql"
    sql_text = migration_path.read_text(encoding="utf-8")
    statements = [chunk.strip() for chunk in sql_text.split(";") if chunk.strip()]

    for statement in statements:
        parsed = sqlglot.parse_one(statement, read="clickhouse")
        assert parsed is not None


def test_portfolio_fixture_valid_schema_and_unique_pairs(fixtures_dir: Path, load_json) -> None:
    """Проверяет структуру портфеля и уникальность пары portfolio_id+ticker."""
    data = load_json(fixtures_dir / "portfolio_sample.json")
    required_keys = {
        "portfolio_id",
        "ticker",
        "quantity",
        "avg_price",
        "sector",
        "instrument_type",
        "currency",
    }
    allowed_sectors = {"нефтегаз", "финансы", "металлургия", "IT", "ритейл", "другое"}
    allowed_types = {"акция", "облигация"}
    allowed_currencies = {"RUB", "USD", "EUR"}

    assert isinstance(data, list)
    assert len(data) == 10

    pairs: list[tuple[str, str]] = []
    for row in data:
        assert set(row.keys()) == required_keys
        assert row["sector"] in allowed_sectors
        assert row["instrument_type"] in allowed_types
        assert row["currency"] in allowed_currencies
        pairs.append((str(row["portfolio_id"]), str(row["ticker"])))

    counts = Counter(pairs)
    duplicates = [pair for pair, count in counts.items() if count > 1]
    assert duplicates == []


def test_candles_fixture_schema_and_ticker_consistency(fixtures_dir: Path, load_json) -> None:
    """Проверяет схему candles-конфига и согласованность тикеров с портфелем."""
    portfolio_rows = load_json(fixtures_dir / "portfolio_sample.json")
    candles_config = load_json(fixtures_dir / "moex_candles_sample.json")

    assert "metadata" in candles_config
    assert "tickers" in candles_config
    assert candles_config["metadata"]["trading_days"] >= 252
    assert candles_config["metadata"]["start_date"]

    portfolio_tickers = {row["ticker"] for row in portfolio_rows}
    candles_tickers = {row["ticker"] for row in candles_config["tickers"]}
    assert portfolio_tickers == candles_tickers


def test_factory_returns_mock_client_when_enabled() -> None:
    """Проверяет, что фабрика возвращает mock-клиент при включенном флаге."""
    settings = Settings(use_mock_clickhouse=True)
    client = get_analytics_data_client(settings)
    assert isinstance(client, MockClickHouseClient)


def test_factory_returns_real_client_when_disabled(monkeypatch) -> None:
    """Проверяет, что фабрика возвращает real-клиент при отключенном mock режиме."""
    class DummyClickHouseConnection:
        def query(self, *_args, **_kwargs):
            raise RuntimeError("Not expected in this test")

    monkeypatch.setattr(
        "servers.analytics_server.clickhouse_client.clickhouse_connect.get_client",
        lambda **_kwargs: DummyClickHouseConnection(),
    )
    settings = Settings(use_mock_clickhouse=False)
    client = get_analytics_data_client(settings)
    assert isinstance(client, ClickHouseClient)


def test_mock_client_reads_portfolio_and_price_history(fixtures_dir: Path) -> None:
    """Проверяет чтение портфеля и генерацию истории в mock режиме."""
    settings = Settings(use_mock_clickhouse=True)
    client = MockClickHouseClient(settings=settings, fixtures_dir=fixtures_dir)

    positions = client.get_portfolio_positions("demo_portfolio")
    assert len(positions) == 10

    tickers = [row["ticker"] for row in positions]
    history = client.get_price_history(tickers)
    assert history

    grouped_rows: dict[str, list[dict]] = defaultdict(list)
    for row in history:
        grouped_rows[str(row["ticker"])].append(row)

    assert set(grouped_rows.keys()) == set(tickers)
    for ticker, rows in grouped_rows.items():
        assert len(rows) >= 252, f"Недостаточно наблюдений для {ticker}"
        previous_date: datetime | None = None
        seen_dates: set[str] = set()
        for candle in rows:
            current_date = datetime.strptime(str(candle["date"]), "%Y-%m-%d")
            if previous_date is not None:
                assert current_date > previous_date
            previous_date = current_date

            iso_date = str(candle["date"])
            assert iso_date not in seen_dates
            seen_dates.add(iso_date)

            low = float(candle["low"])
            high = float(candle["high"])
            open_price = float(candle["open"])
            close = float(candle["close"])
            assert low <= open_price <= high
            assert low <= close <= high


def test_mock_execute_select_supports_read_only_subset(fixtures_dir: Path) -> None:
    """Проверяет базовую поддержку SELECT в mock-клиенте."""
    settings = Settings(use_mock_clickhouse=True)
    client = MockClickHouseClient(settings=settings, fixtures_dir=fixtures_dir)

    result = client.execute_select("SELECT * FROM portfolios LIMIT 5")
    assert result["row_count"] == 5
    assert result["columns"][0] == "portfolio_id"

