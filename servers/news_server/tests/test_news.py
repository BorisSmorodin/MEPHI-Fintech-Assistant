"""Юнит-тесты news_server."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from fastmcp.exceptions import ToolError

from config.settings import Settings
from servers.news_server.rss_fetcher import NEWS_SOURCES, NewsFetcher, NewsFetchError
from servers.news_server.sentiment import classify_sentiment, compute_sentiment_score
from servers.news_server.server import (
    fetch_news,
    get_cb_key_rate,
    get_macro_calendar,
    get_market_sentiment,
)


class DummyResponse:
    """Тестовый HTTP response."""

    def __init__(self, content: bytes = b"", status_code: int = 200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _settings() -> Settings:
    return Settings(news_server_cache_ttl=300, news_http_timeout_sec=10)


def test_sentiment_classification_and_score() -> None:
    """Проверяет эвристическую классификацию и score."""
    assert classify_sentiment("Компания показала рост прибыли и дивиденд") == "positive"
    assert classify_sentiment("Падение выручки и убыток, downgrade прогноза") == "negative"
    assert classify_sentiment("Новость без ярко выраженного сигнала") == "neutral"
    assert compute_sentiment_score(5, 2, 10) == 0.3
    assert compute_sentiment_score(0, 0, 0) == 0.0


def test_rss_fetch_and_dedup_sort(monkeypatch) -> None:
    """Проверяет разбор RSS, дедупликацию и сортировку."""
    fetcher = NewsFetcher(settings=_settings(), session=requests.Session())

    monkeypatch.setattr(
        fetcher.session,
        "get",
        lambda *_args, **_kwargs: DummyResponse(content=b"<rss />"),
    )
    monkeypatch.setattr(
        "servers.news_server.rss_fetcher.feedparser.parse",
        lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="SBER рост прибыли",
                    link="https://example.com/1",
                    published="Tue, 14 Apr 2026 12:00:00 +0000",
                    summary="Позитивная отчетность",
                ),
                SimpleNamespace(
                    title="SBER рост прибыли",
                    link="https://example.com/1dup",
                    published="Tue, 14 Apr 2026 11:00:00 +0000",
                    summary="Дубликат",
                ),
                SimpleNamespace(
                    title="SBER снижение маржи",
                    link="https://example.com/2",
                    published="Tue, 14 Apr 2026 13:00:00 +0000",
                    summary="Негатив",
                ),
            ]
        ),
    )
    result = fetcher.fetch_news(query="SBER", sources=["cbr"], limit=10)
    assert len(result) == 2
    assert result[0]["title"] == "SBER снижение маржи"
    assert result[1]["title"] == "SBER рост прибыли"
    assert result[0]["source"] == "cbr"
    assert result[0]["source_trust"] == NEWS_SOURCES["cbr"]["trust"]


def test_rss_cache_hit_and_expiration(monkeypatch) -> None:
    """Проверяет miss/hit/expiration кэша RSS."""
    now = {"value": 1000.0}

    def time_provider() -> float:
        return now["value"]

    fetcher = NewsFetcher(settings=_settings(), session=requests.Session(), time_provider=time_provider)
    call_counter = {"count": 0}

    def fake_get(*_args, **_kwargs):
        call_counter["count"] += 1
        return DummyResponse(content=b"<rss />")

    monkeypatch.setattr(fetcher.session, "get", fake_get)
    monkeypatch.setattr(
        "servers.news_server.rss_fetcher.feedparser.parse",
        lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="GAZP новость",
                    link="https://example.com",
                    published="Tue, 14 Apr 2026 10:00:00 +0000",
                    summary="summary",
                )
            ]
        ),
    )

    first = fetcher.fetch_news(query="GAZP", sources=["cbr"], limit=5)
    second = fetcher.fetch_news(query="GAZP", sources=["cbr"], limit=5)
    assert len(first) == len(second) == 1
    assert call_counter["count"] == 1

    now["value"] += 301.0
    third = fetcher.fetch_news(query="GAZP", sources=["cbr"], limit=5)
    assert len(third) == 1
    assert call_counter["count"] == 2


def test_unavailable_source_fallback(monkeypatch) -> None:
    """Проверяет устойчивость при недоступности одного источника."""
    fetcher = NewsFetcher(settings=_settings(), session=requests.Session())

    def fake_get(url: str, **_kwargs):
        if "interfax" in url:
            raise requests.ConnectionError("down")
        return DummyResponse(content=b"<rss />")

    monkeypatch.setattr(fetcher.session, "get", fake_get)
    monkeypatch.setattr(
        "servers.news_server.rss_fetcher.feedparser.parse",
        lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="LKOH новость",
                    link="https://example.com/lkoh",
                    published="Tue, 14 Apr 2026 12:00:00 +0000",
                    summary="ok",
                )
            ]
        ),
    )
    result = fetcher.fetch_news(query="LKOH", sources=["interfax", "cbr"], limit=10)
    assert len(result) == 1
    assert result[0]["source"] == "cbr"
    diagnostics = fetcher.last_fetch_diagnostics
    assert diagnostics["source_errors"] == 1
    assert diagnostics["degraded"] is False


def test_sber_alias_not_inside_kenigsberg_substring(monkeypatch) -> None:
    """«сбер» не должен матчить подстроку в «кенигсберг*»; префикс «сбербанк» — да."""
    fetcher = NewsFetcher(settings=_settings(), session=requests.Session())
    monkeypatch.setattr(fetcher.session, "get", lambda *_args, **_kwargs: DummyResponse(content=b"<rss />"))
    monkeypatch.setattr(
        "servers.news_server.rss_fetcher.feedparser.parse",
        lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title='Мединский против кенигсберских "флешбеков"',
                    link="https://example.com/bad",
                    published="Tue, 14 Apr 2026 13:00:00 +0000",
                    summary="",
                ),
                SimpleNamespace(
                    title="Сбербанк подвел итоги квартала",
                    link="https://example.com/good",
                    published="Tue, 14 Apr 2026 12:00:00 +0000",
                    summary="Дивиденды",
                ),
            ]
        ),
    )
    result = fetcher.fetch_news(query="SBER", sources=["interfax"], limit=10)
    assert len(result) == 1
    assert "Сбербанк" in result[0]["title"]


def test_source_alias_no_irrelevant_recency_fallback(monkeypatch) -> None:
    """Alias источника (cbonds→finam); без релевантных совпадений не подставляем случайные топ-N."""
    fetcher = NewsFetcher(settings=_settings(), session=requests.Session())
    monkeypatch.setattr(fetcher.session, "get", lambda *_args, **_kwargs: DummyResponse(content=b"<rss />"))
    monkeypatch.setattr(
        "servers.news_server.rss_fetcher.feedparser.parse",
        lambda *_args, **_kwargs: SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="Обзор рынка акций",
                    link="https://example.com/market",
                    published="Tue, 14 Apr 2026 12:00:00 +0000",
                    summary="Общая рыночная ситуация",
                )
            ]
        ),
    )
    result = fetcher.fetch_news(query="GAZP", sources=["cbonds"], limit=5)
    assert len(result) == 0
    diagnostics = fetcher.last_fetch_diagnostics
    assert diagnostics["fallback_mode"] == "or_query_terms"


@pytest.mark.asyncio
async def test_fetch_news_tool_raises_when_sources_degraded_and_empty(monkeypatch) -> None:
    """Проверяет диагностическую ToolError при пустом результате и деградации источников."""

    class FailingFetcher:
        last_fetch_diagnostics = {
            "unavailable_sources": ["cbr", "interfax"],
            "degraded": True,
            "fallback_mode": "none",
        }

        def fetch_news(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr("servers.news_server.server._get_fetcher", lambda: FailingFetcher())
    with pytest.raises(ToolError) as error:
        await fetch_news("GAZP", limit=10)
    assert "недоступны" in str(error.value)


@pytest.mark.asyncio
async def test_news_server_tools_smoke(monkeypatch) -> None:
    """Smoke-тест MCP-инструментов news_server."""
    now = datetime.now(timezone.utc)

    class FakeFetcher:
        last_fetch_diagnostics = {
            "unavailable_sources": [],
            "degraded": False,
            "fallback_mode": "none",
        }

        def fetch_news(self, query: str, sources: list[str] | None = None, limit: int = 10):
            _ = query, sources, limit
            return [
                {
                    "title": "SBER рост прибыли",
                    "url": "https://example.com/1",
                    "published": (now - timedelta(days=1)).isoformat(),
                    "source": "cbr",
                    "source_trust": "HIGH",
                    "summary": "рост прибыли",
                },
                {
                    "title": "SBER падение маржи",
                    "url": "https://example.com/2",
                    "published": (now - timedelta(days=2)).isoformat(),
                    "source": "rbc",
                    "source_trust": "MEDIUM",
                    "summary": "падение маржи",
                },
            ]

    monkeypatch.setattr("servers.news_server.server._get_fetcher", lambda: FakeFetcher())

    rows = await fetch_news("SBER", limit=10)
    assert rows[0]["sentiment"] in {"positive", "negative", "neutral"}

    rate = await get_cb_key_rate()
    assert "rate" in rate and "next_meeting" in rate

    sentiment = await get_market_sentiment("SBER")
    assert sentiment["ticker"] == "SBER"
    assert -1.0 <= sentiment["score"] <= 1.0

    macro = await get_macro_calendar("2026-01-01", "2026-12-31")
    assert isinstance(macro, list)
    assert len(macro) >= 1


@pytest.mark.asyncio
async def test_news_tools_validate_inputs() -> None:
    """Проверяет негативные сценарии валидации news-инструментов."""
    with pytest.raises(ToolError):
        await fetch_news("   ")
    with pytest.raises(ToolError):
        await get_macro_calendar("2026-10-01", "2026-01-01")

