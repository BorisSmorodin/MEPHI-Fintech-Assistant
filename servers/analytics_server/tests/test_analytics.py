"""Тесты инфраструктуры analytics_server (Этап 1)."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pytest
import sqlglot
from fastmcp.exceptions import ToolError

from config.settings import Settings
from servers.analytics_server.clickhouse_client import (
    ClickHouseClient,
    MockClickHouseClient,
    get_analytics_data_client,
)
from servers.analytics_server.server import (
    _build_portfolio_summary,
    _calculate_risk_metrics,
    _execute_analytics_query,
    _run_stress_test,
    _validate_and_rewrite_select_query,
    calculate_risk_metrics,
    execute_analytics_query,
    get_portfolio_summary,
    run_stress_test,
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
    class DummyResult:
        column_names = ("one",)
        result_rows = [(1,)]

    class DummyClickHouseConnection:
        def query(self, *_args, **_kwargs):
            return DummyResult()

    monkeypatch.setattr(
        "servers.analytics_server.clickhouse_client.clickhouse_connect.get_client",
        lambda **_kwargs: DummyClickHouseConnection(),
    )
    settings = Settings(use_mock_clickhouse=False)
    client = get_analytics_data_client(settings)
    assert isinstance(client, ClickHouseClient)


def test_factory_falls_back_to_mock_when_clickhouse_unavailable(monkeypatch) -> None:
    """Проверяет fallback real->mock при недоступном ClickHouse."""

    class UnavailableClickHouseConnection:
        def query(self, *_args, **_kwargs):
            raise ConnectionError("clickhouse unavailable")

    monkeypatch.setattr(
        "servers.analytics_server.clickhouse_client.clickhouse_connect.get_client",
        lambda **_kwargs: UnavailableClickHouseConnection(),
    )
    settings = Settings(
        use_mock_clickhouse=False,
        allow_mock_fallback_on_clickhouse_error=True,
    )
    client = get_analytics_data_client(settings)
    assert isinstance(client, MockClickHouseClient)


def test_factory_raises_when_clickhouse_unavailable_and_fallback_disabled(monkeypatch) -> None:
    """Проверяет ошибку при недоступном ClickHouse и выключенном fallback."""

    class UnavailableClickHouseConnection:
        def query(self, *_args, **_kwargs):
            raise ConnectionError("clickhouse unavailable")

    monkeypatch.setattr(
        "servers.analytics_server.clickhouse_client.clickhouse_connect.get_client",
        lambda **_kwargs: UnavailableClickHouseConnection(),
    )
    settings = Settings(
        use_mock_clickhouse=False,
        allow_mock_fallback_on_clickhouse_error=False,
    )
    with pytest.raises(ConnectionError):
        get_analytics_data_client(settings)


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


def test_get_portfolio_summary_contract(fixtures_dir: Path) -> None:
    """Проверяет формат ответа get_portfolio_summary."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    result = _build_portfolio_summary("demo_portfolio", client)
    assert result["portfolio_id"] == "demo_portfolio"
    assert result["total_value"] > 0
    assert len(result["positions"]) == 10
    assert {"by_sector", "by_type", "by_currency"} <= set(result["allocation"].keys())


def test_calculate_risk_metrics_contains_required_indicators(fixtures_dir: Path) -> None:
    """Проверяет наличие и диапазоны ключевых риск-метрик."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    result = _calculate_risk_metrics("demo_portfolio", confidence=0.95, client=client)
    assert result["var_historical"]["value_rub"] >= 0
    assert result["var_parametric"]["value_rub"] >= 0
    assert result["cvar"]["value_rub"] >= 0
    assert result["volatility"]["value_annual"] > 0
    assert 0 <= result["hhi"]["positions"]["value"] <= 1
    assert 0 <= result["hhi"]["sectors"]["value"] <= 1
    assert result["max_drawdown"]["value"] >= 0


def test_run_stress_test_scenarios(fixtures_dir: Path) -> None:
    """Проверяет выполнение всех стресс-сценариев."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)

    result_index = _run_stress_test("demo_portfolio", "index_drop", 20.0, None, client)
    assert result_index["scenario"] == "index_drop"
    assert result_index["total_loss_rub"] > 0

    result_rate = _run_stress_test("demo_portfolio", "rate_hike", 2.0, None, client)
    assert result_rate["scenario"] == "rate_hike"
    assert result_rate["affected_positions_count"] > 0

    result_sector = _run_stress_test("demo_portfolio", "sector_decline", 15.0, "нефтегаз", client)
    assert result_sector["scenario"] == "sector_decline"
    assert result_sector["target_sector"] == "нефтегаз"

    result_yandex = _run_stress_test("demo_portfolio", "sector_decline", 20.0, "Yandex", client)
    assert result_yandex["affected_positions_count"] > 0
    assert result_yandex["total_loss_rub"] > 0
    tickers_hit = {p["ticker"] for p in result_yandex["affected_positions"]}
    assert "YNDX" in tickers_hit
    assert result_yandex["target_sector"] == "IT"


def test_sql_validation_rejects_non_select() -> None:
    """Проверяет запрет non-SELECT SQL запросов."""
    with pytest.raises(Exception):
        _validate_and_rewrite_select_query("DELETE FROM portfolios")
    with pytest.raises(Exception):
        _validate_and_rewrite_select_query("CREATE TABLE t(x Int32)")
    with pytest.raises(Exception):
        _validate_and_rewrite_select_query("SELECT * FROM portfolios; SELECT * FROM price_history")
    with pytest.raises(Exception):
        _validate_and_rewrite_select_query("SELECT * FROM portfolios; DROP TABLE portfolios")


def test_sql_validation_adds_limit() -> None:
    """Проверяет автодобавление LIMIT 1000."""
    query = _validate_and_rewrite_select_query("SELECT ticker FROM portfolios")
    assert "LIMIT 1000" in query.upper()


def test_sql_validation_keeps_existing_limit() -> None:
    """Проверяет сохранение явно заданного лимита."""
    query = _validate_and_rewrite_select_query("SELECT ticker FROM portfolios LIMIT 25")
    assert "LIMIT 25" in query.upper()


def test_execute_analytics_query_returns_limited_rows(fixtures_dir: Path) -> None:
    """Проверяет безопасное выполнение SQL в mock-режиме."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    result = _execute_analytics_query("SELECT * FROM portfolios", client)
    assert result["row_count"] == 10
    assert "rows" in result


@pytest.mark.asyncio
async def test_mcp_tool_smoke_in_mock_mode(fixtures_dir: Path, monkeypatch) -> None:
    """Smoke-тест асинхронных MCP-инструментов analytics_server в mock режиме."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    monkeypatch.setattr("servers.analytics_server.server._get_client", lambda: client)

    summary = await get_portfolio_summary("demo_portfolio")
    assert summary["total_value"] > 0

    metrics = await calculate_risk_metrics("demo_portfolio", 0.95)
    assert metrics["volatility"]["value_annual"] > 0

    stress = await run_stress_test("demo_portfolio", "index_drop", 10.0)
    assert stress["total_loss_rub"] > 0

    sql_result = await execute_analytics_query("SELECT * FROM portfolios")
    assert sql_result["row_count"] == 10


@pytest.mark.asyncio
async def test_execute_analytics_query_timeout_is_safely_wrapped(monkeypatch) -> None:
    """Проверяет безопасную обработку таймаута аналитического SQL."""

    class TimeoutClient:
        def execute_select(self, _query: str):
            raise TimeoutError("query exceeded 30s and includes internal details")

    monkeypatch.setattr("servers.analytics_server.server._get_client", lambda: TimeoutClient())
    with pytest.raises(ToolError) as error:
        await execute_analytics_query("SELECT * FROM portfolios")
    assert "Ошибка выполнения аналитического запроса" in str(error.value)


@pytest.mark.asyncio
async def test_calculate_risk_metrics_invalid_confidence_is_wrapped(fixtures_dir: Path, monkeypatch) -> None:
    """Проверяет безопасную обработку невалидного confidence."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    monkeypatch.setattr("servers.analytics_server.server._get_client", lambda: client)
    with pytest.raises(ToolError) as error:
        await calculate_risk_metrics("demo_portfolio", confidence=0.3)
    assert "Ошибка расчета риск-метрик" in str(error.value)


@pytest.mark.asyncio
async def test_run_stress_test_invalid_scenario_is_wrapped(fixtures_dir: Path, monkeypatch) -> None:
    """Проверяет безопасную обработку невалидного сценария стресс-теста."""
    client = MockClickHouseClient(settings=Settings(use_mock_clickhouse=True), fixtures_dir=fixtures_dir)
    monkeypatch.setattr("servers.analytics_server.server._get_client", lambda: client)
    with pytest.raises(ToolError) as error:
        await run_stress_test("demo_portfolio", scenario="bad_scenario", magnitude=10.0)
    assert "Ошибка стресс-тестирования" in str(error.value)

