"""End-to-end сценарии для инвестиционного ассистента."""

from __future__ import annotations

import pytest

from orchestrator.graph import run_query
from ui.cli import format_debug_payload, process_cli_input
from ui.streamlit_app import build_effective_query, execute_streamlit_query


@pytest.fixture
def fake_mcp_client():
    """Возвращает mock MCP клиент для E2E smoke."""

    class FakeMCPClient:
        async def call_tool(self, tool_name, tool_args):
            if tool_name == "get_stock_quote":
                return {"SECID": tool_args["ticker"], "LAST": 320.0}
            if tool_name == "fetch_news":
                return [
                    {
                        "title": "GAZP новость",
                        "url": "https://example.com",
                        "published": "2026-01-01T10:00:00+00:00",
                        "source": "interfax",
                        "source_trust": "HIGH",
                        "summary": "рост",
                        "sentiment": "positive",
                    }
                ]
            if tool_name == "get_market_sentiment":
                return {"ticker": tool_args["ticker"], "score": 0.25}
            if tool_name == "get_portfolio_summary":
                return {"portfolio_id": tool_args["portfolio_id"], "total_value": 100000.0}
            if tool_name == "calculate_risk_metrics":
                return {"volatility": {"value_annual": 0.2}}
            if tool_name == "run_stress_test":
                return {"scenario": "index_drop", "total_loss_rub": 15000.0}
            return {}

    return FakeMCPClient()


@pytest.fixture(autouse=True)
def patch_mcp_client(monkeypatch, fake_mcp_client):
    """Подменяет MCP-клиент во всех executor-узлах."""
    monkeypatch.setattr("orchestrator.nodes.market_executor.get_mcp_client", lambda: fake_mcp_client)
    monkeypatch.setattr("orchestrator.nodes.news_executor.get_mcp_client", lambda: fake_mcp_client)
    monkeypatch.setattr("orchestrator.nodes.analytics_executor.get_mcp_client", lambda: fake_mcp_client)

    async def _fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("orchestrator.nodes.market_executor.asyncio.sleep", _fake_sleep)
    monkeypatch.setattr("orchestrator.nodes.planner_node._try_llm_plan", lambda _state: None)


@pytest.mark.asyncio
async def test_e2e_market_only() -> None:
    """Сценарий market-only."""
    result = await run_query(
        "Покажи котировку SBER",
        scenario_name="market_only",
        expected_servers={"market"},
    )
    assert result["final_answer"]
    assert "get_stock_quote" in result["market_data"]
    assert result["quality_metrics"]["tool_selection_correct"] is True
    assert "Котировка SBER" in result["final_answer"]
    assert "Что запросил пользователь" in result["final_answer"]


@pytest.mark.asyncio
async def test_e2e_news_only() -> None:
    """Сценарий news-only."""
    result = await run_query(
        "Последние новости по Газпрому",
        scenario_name="news_only",
        expected_servers={"news"},
    )
    assert result["final_answer"]
    assert len(result["news_data"]) >= 1
    assert result["quality_metrics"]["tool_selection_correct"] is True
    assert "GAZP новость" in result["final_answer"]
    assert "тональность" in result["final_answer"]


@pytest.mark.asyncio
async def test_e2e_risk_only() -> None:
    """Сценарий risk-only."""
    result = await run_query(
        "Оцени риск портфеля demo_portfolio",
        scenario_name="risk_only",
        expected_servers={"analytics"},
    )
    assert result["final_answer"]
    assert "calculate_risk_metrics" in result["portfolio_metrics"]
    assert result["quality_metrics"]["tool_selection_correct"] is True
    assert "Волатильность" in result["final_answer"]
    assert "Практический вывод" in result["final_answer"]
    assert not any("missing required argument" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_e2e_stress_imoex_minus_20() -> None:
    """Сценарий стресс-теста IMOEX -20% из ТЗ."""
    result = await run_query(
        "Проведи стресс-тест портфеля demo_portfolio при падении IMOEX на 20%",
        scenario_name="stress_imoex_minus_20",
        expected_servers={"analytics"},
    )
    assert result["final_answer"]
    stress = result["portfolio_metrics"]["run_stress_test"]
    assert stress["scenario"] == "index_drop"
    assert stress["total_loss_rub"] > 0
    assert result["quality_metrics"]["tool_selection_correct"] is True
    assert "Стресс-тест" in result["final_answer"]
    assert "потери" in result["final_answer"]
    assert not any("missing required argument" in warning.lower() for warning in result["warnings"])


@pytest.mark.asyncio
async def test_e2e_portfolio_with_oilgas_news() -> None:
    """Комплексный сценарий портфель + новости нефтегаза из ТЗ."""
    result = await run_query(
        "Оцени риск портфеля demo_portfolio с учетом новостей нефтегаза и котировки GAZP",
        scenario_name="portfolio_oilgas_news",
        expected_servers={"market", "news", "analytics"},
    )
    assert result["final_answer"]
    assert result["market_data"]
    assert result["news_data"]
    assert result["portfolio_metrics"]
    assert result["quality_metrics"]["tool_selection_correct"] is True
    assert "Котировка GAZP" in result["final_answer"]
    assert "GAZP новость" in result["final_answer"]


def test_cli_command_parsing_and_debug() -> None:
    """Проверяет команды REPL и debug-представление."""
    assert process_cli_input("", None).action == "noop"
    assert process_cli_input("exit", None).action == "exit"
    assert process_cli_input("clear", None).action == "clear"

    debug_result = process_cli_input("debug", {"query_type": "complex", "plan": [], "warnings": []})
    assert debug_result.action == "debug"
    assert "query_type" in (debug_result.message or "")
    assert "complex" in (debug_result.message or "")
    assert "пока нет выполненных запросов" in format_debug_payload(None)


def test_streamlit_query_builder() -> None:
    """Проверяет формирование итогового query для UI."""
    original = "Оцени риск портфеля demo_portfolio"
    assert build_effective_query(original, "demo_portfolio") == original
    assert "portfolio_id: p1" in build_effective_query("Оцени риск", "p1")


@pytest.mark.asyncio
async def test_streamlit_handler_smoke(monkeypatch) -> None:
    """Проверяет handler-level smoke для Streamlit без запуска браузера."""

    async def _fake_run_query(user_query: str, state_overrides=None):
        return {
            "user_query": user_query,
            "final_answer": "Ответ готов.",
            "warnings": [],
            "error_count": 0,
            "query_type": "complex",
            "plan": [],
            "state_overrides": state_overrides,
        }

    monkeypatch.setattr("ui.streamlit_app.run_query", _fake_run_query)
    result = await execute_streamlit_query(
        user_query="Покажи котировку SBER",
        portfolio_id="demo_portfolio",
        selected_model="gpt-oss-120b",
    )
    assert result["final_answer"] == "Ответ готов."
    assert any("UI model preference" in row for row in result["warnings"])


@pytest.mark.asyncio
async def test_e2e_degradation_when_news_unavailable(monkeypatch) -> None:
    """Проверяет деградацию графа при недоступности news-инструмента."""

    class MixedClient:
        async def call_tool(self, tool_name, tool_args):
            if tool_name == "fetch_news":
                raise Exception("rss unavailable")
            if tool_name == "get_stock_quote":
                return {"SECID": tool_args["ticker"], "LAST": 300.0}
            if tool_name == "calculate_risk_metrics":
                return {"volatility": {"value_annual": 0.21}}
            if tool_name == "get_portfolio_summary":
                return {"portfolio_id": "demo_portfolio", "total_value": 99999.0}
            return {}

    mixed_client = MixedClient()
    monkeypatch.setattr("orchestrator.nodes.market_executor.get_mcp_client", lambda: mixed_client)
    monkeypatch.setattr("orchestrator.nodes.news_executor.get_mcp_client", lambda: mixed_client)
    monkeypatch.setattr("orchestrator.nodes.analytics_executor.get_mcp_client", lambda: mixed_client)

    async def _fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("orchestrator.nodes.market_executor.asyncio.sleep", _fake_sleep)

    result = await run_query("Покажи котировку SBER, новости и оцени риск портфеля demo_portfolio")
    assert result["final_answer"]
    assert isinstance(result.get("error_count"), int)
    assert "ограничен" in result["final_answer"]

