"""End-to-end сценарии для инвестиционного ассистента."""

from __future__ import annotations

import pytest

from orchestrator.graph import run_query


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

