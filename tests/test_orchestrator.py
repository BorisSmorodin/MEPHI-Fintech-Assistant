"""Интеграционные тесты оркестратора."""

from __future__ import annotations

import pytest

from orchestrator.graph import run_query
from orchestrator.nodes.analytics_executor import analytics_executor
from orchestrator.nodes.input_node import input_node
from orchestrator.nodes.market_executor import market_executor
from orchestrator.nodes.news_executor import news_executor
from orchestrator.nodes.planner_node import planner_node, route_planner
from orchestrator.state import initial_state


@pytest.mark.asyncio
async def test_input_node_validation_and_classification() -> None:
    """Проверяет валидацию и классификацию input_node."""
    with pytest.raises(ValueError):
        await input_node({"user_query": ""})

    result = await input_node({"user_query": "Покажи котировку SBER"})
    assert result["query_type"] == "market_monitor"
    assert "SBER" in result["extracted_tickers"]


@pytest.mark.asyncio
async def test_planner_routing() -> None:
    """Проверяет маршрутизацию planner."""
    state = initial_state("Покажи котировку SBER")
    state["query_type"] = "market_monitor"
    state["extracted_tickers"] = ["SBER"]

    result = await planner_node(state)
    assert result["next_node"] == "market_executor"
    assert route_planner(result) == "market_executor"


@pytest.mark.asyncio
async def test_market_executor_retry_and_no_retry(monkeypatch) -> None:
    """Проверяет retry и no-retry поведение market_executor."""

    class RetryClient:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise Exception("temporary failure")
            return {"SECID": "SBER", "LAST": 100.0}

    retry_client = RetryClient()
    monkeypatch.setattr("orchestrator.nodes.market_executor.get_mcp_client", lambda: retry_client)

    async def _fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("orchestrator.nodes.market_executor.asyncio.sleep", _fake_sleep)

    state = initial_state("Покажи котировку SBER")
    state["plan"] = [
        {
            "step_number": 1,
            "description": "quote",
            "target_server": "market_executor",
            "tool_name": "get_stock_quote",
            "tool_args": {"ticker": "SBER"},
        }
    ]
    result = await market_executor(state)
    assert retry_client.calls == 3
    assert result["market_data"]["get_stock_quote"]["SECID"] == "SBER"

    class NotFoundClient:
        async def call_tool(self, *_args, **_kwargs):
            raise Exception("Тикер SBER не найден на TQBR")

    monkeypatch.setattr("orchestrator.nodes.market_executor.get_mcp_client", lambda: NotFoundClient())
    state_not_found = initial_state("Покажи котировку")
    state_not_found["plan"] = state["plan"]
    result_not_found = await market_executor(state_not_found)
    assert result_not_found["error_count"] == 1


@pytest.mark.asyncio
async def test_news_executor_degradation(monkeypatch) -> None:
    """Проверяет деградацию news_executor при серии ошибок."""

    class FailingClient:
        async def call_tool(self, *_args, **_kwargs):
            raise Exception("source unavailable")

    monkeypatch.setattr("orchestrator.nodes.news_executor.get_mcp_client", lambda: FailingClient())
    state = initial_state("Новости по SBER")
    state["error_count"] = 2
    state["plan"] = [
        {
            "step_number": 1,
            "description": "news",
            "target_server": "news_executor",
            "tool_name": "fetch_news",
            "tool_args": {"query": "SBER", "limit": 10},
        }
    ]
    result = await news_executor(state)
    assert result["error_count"] == 3
    assert result["warnings"]


@pytest.mark.asyncio
async def test_analytics_executor_fallback(monkeypatch) -> None:
    """Проверяет fallback analytics_executor при ошибке risk metrics."""

    class AnalyticsClient:
        async def call_tool(self, tool_name, _args):
            if tool_name == "calculate_risk_metrics":
                raise Exception("risk failed")
            if tool_name == "get_portfolio_summary":
                return {"portfolio_id": "demo_portfolio"}
            return {}

    monkeypatch.setattr("orchestrator.nodes.analytics_executor.get_mcp_client", lambda: AnalyticsClient())
    state = initial_state("Оцени риск портфеля demo_portfolio")
    state["plan"] = [
        {
            "step_number": 1,
            "description": "risk",
            "target_server": "analytics_executor",
            "tool_name": "calculate_risk_metrics",
            "tool_args": {"portfolio_id": "demo_portfolio", "confidence": 0.95},
        }
    ]
    result = await analytics_executor(state)
    assert "fallback_portfolio_summary" in result["portfolio_metrics"]


@pytest.mark.asyncio
async def test_graph_integration_with_mock_mcp(monkeypatch) -> None:
    """Проверяет интеграционный проход графа на mock MCP."""

    class FakeMCPClient:
        async def call_tool(self, tool_name, tool_args):
            if tool_name == "get_stock_quote":
                return {"SECID": tool_args["ticker"], "LAST": 321.0}
            if tool_name == "fetch_news":
                return [
                    {
                        "title": "SBER рост прибыли",
                        "url": "https://example.com",
                        "published": "2026-01-01T10:00:00+00:00",
                        "source": "cbr",
                        "source_trust": "HIGH",
                        "summary": "рост",
                        "sentiment": "positive",
                    }
                ]
            if tool_name == "get_market_sentiment":
                return {"ticker": "SBER", "score": 0.5}
            if tool_name == "get_portfolio_summary":
                return {"portfolio_id": "demo_portfolio", "total_value": 100000.0}
            if tool_name == "calculate_risk_metrics":
                return {"volatility": {"value_annual": 0.2}}
            if tool_name == "run_stress_test":
                return {"scenario": "index_drop", "total_loss_rub": 10000.0}
            return {}

    fake_client = FakeMCPClient()
    monkeypatch.setattr("orchestrator.nodes.market_executor.get_mcp_client", lambda: fake_client)
    monkeypatch.setattr("orchestrator.nodes.news_executor.get_mcp_client", lambda: fake_client)
    monkeypatch.setattr("orchestrator.nodes.analytics_executor.get_mcp_client", lambda: fake_client)

    async def _fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr("orchestrator.nodes.market_executor.asyncio.sleep", _fake_sleep)

    result = await run_query("Покажи котировку SBER и новости, оцени риск портфеля demo_portfolio")
    assert result["final_answer"] is not None

