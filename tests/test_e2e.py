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


@pytest.mark.asyncio
async def test_e2e_market_only() -> None:
    """Сценарий market-only."""
    result = await run_query("Покажи котировку SBER")
    assert result["final_answer"]


@pytest.mark.asyncio
async def test_e2e_news_only() -> None:
    """Сценарий news-only."""
    result = await run_query("Последние новости по Газпрому")
    assert result["final_answer"]


@pytest.mark.asyncio
async def test_e2e_risk_only() -> None:
    """Сценарий risk-only."""
    result = await run_query("Оцени риск портфеля demo_portfolio")
    assert result["final_answer"]


@pytest.mark.asyncio
async def test_e2e_complex() -> None:
    """Комплексный сценарий с несколькими серверами."""
    result = await run_query("Оцени портфель demo_portfolio с учетом новостей по GAZP и котировки SBER")
    assert result["final_answer"]


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

