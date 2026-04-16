"""Точка входа MCP-сервера новостей."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
import structlog

from config.logging_setup import configure_structlog_for_mcp_stdio
from servers.news_server.rss_fetcher import NEWS_SOURCES, NewsFetchError, NewsFetcher, get_news_fetcher
from servers.news_server.sentiment import classify_sentiment, compute_sentiment_score

configure_structlog_for_mcp_stdio()
log = structlog.get_logger()
mcp = FastMCP("news_server")
_fetcher: NewsFetcher | None = None

_RATE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")

# В news_server не используются вызовы LLM, только эвристическая обработка.
MACRO_CALENDAR = [
    {"date": "2026-04-24", "event": "Заседание Совета директоров Банка России", "impact": "HIGH", "previous": "16.00%"},
    {"date": "2026-06-05", "event": "Публикация среднесрочного макропрогноза ЦБ РФ", "impact": "MEDIUM", "previous": "апрель 2026"},
    {"date": "2026-07-24", "event": "Заседание Совета директоров Банка России", "impact": "HIGH", "previous": "16.00%"},
    {"date": "2026-10-23", "event": "Заседание Совета директоров Банка России", "impact": "HIGH", "previous": "16.00%"},
    {"date": "2026-12-18", "event": "Заседание Совета директоров Банка России", "impact": "HIGH", "previous": "16.00%"},
]


def _get_fetcher() -> NewsFetcher:
    """Возвращает singleton-экземпляр NewsFetcher."""
    global _fetcher
    if _fetcher is None:
        _fetcher = get_news_fetcher()
    return _fetcher


def _parse_iso_datetime(value: str) -> datetime:
    """Парсит ISO дату и нормализует tz."""
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_date(date_text: str, field_name: str) -> datetime:
    """Проверяет формат даты YYYY-MM-DD."""
    try:
        parsed = datetime.strptime(date_text.strip(), "%Y-%m-%d")
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise ToolError(f"{field_name} должен быть в формате YYYY-MM-DD") from error


def _extract_rate_from_text(text: str) -> float | None:
    """Извлекает число ставки из текста."""
    match = _RATE_RE.search(text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
async def fetch_news(query: str, sources: list[str] | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Ищет новости по запросу, добавляет trust-уровень и эвристическую тональность."""
    log.info("mcp_tool_called", tool="fetch_news", query=query, sources=sources, limit=limit)
    if len(query.strip()) == 0:
        raise ToolError("Параметр query не может быть пустым.")
    fetcher = _get_fetcher()
    try:
        rows = fetcher.fetch_news(query=query, sources=sources, limit=limit)
    except NewsFetchError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        log.error("fetch_news_failed", error=str(error))
        raise ToolError(f"Ошибка получения новостей: {error}") from error

    diagnostics = fetcher.last_fetch_diagnostics
    unavailable_sources = diagnostics.get("unavailable_sources", [])
    if diagnostics.get("degraded"):
        log.warning(
            "fetch_news_degraded_mode",
            query=query,
            unavailable_sources=unavailable_sources,
            fallback_mode=diagnostics.get("fallback_mode"),
        )

    if not rows and unavailable_sources:
        raise ToolError(
            "Новостные источники частично/полностью недоступны: "
            f"{', '.join(unavailable_sources)}. Попробуйте повторить запрос позже."
        )

    payload: list[dict[str, Any]] = []
    for row in rows:
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        payload.append(
            {
                **row,
                "sentiment": classify_sentiment(text),
            }
        )
    return payload


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
async def get_cb_key_rate() -> dict[str, Any]:
    """Возвращает текущую ключевую ставку ЦБ РФ и ближайшую дату заседания."""
    log.info("mcp_tool_called", tool="get_cb_key_rate")
    try:
        rows = _get_fetcher().fetch_news(query="ключевая ставка", sources=["cbr"], limit=20)
    except NewsFetchError as error:
        log.warning("cb_rate_source_unavailable", error=str(error))
        rows = []
    except Exception as error:
        raise ToolError(f"Ошибка запроса ключевой ставки: {error}") from error

    rate = None
    since_date = ""
    for row in rows:
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        parsed_rate = _extract_rate_from_text(text)
        if parsed_rate is not None:
            rate = parsed_rate
            since_date = row.get("published", "")[:10]
            break

    next_meeting = next((event["date"] for event in MACRO_CALENDAR if event["impact"] == "HIGH"), "")
    if rate is None:
        return {"rate": 16.0, "since_date": since_date or "", "next_meeting": next_meeting}
    return {"rate": rate, "since_date": since_date, "next_meeting": next_meeting}


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
async def get_market_sentiment(ticker: str) -> dict[str, Any]:
    """Возвращает агрегированную тональность новостей по тикеру за 7 дней."""
    normalized_ticker = ticker.strip().upper()
    log.info("mcp_tool_called", tool="get_market_sentiment", ticker=normalized_ticker)
    if not normalized_ticker:
        raise ToolError("ticker не может быть пустым.")
    try:
        rows = await fetch_news(query=normalized_ticker, limit=100)
    except ToolError:
        raise
    except Exception as error:
        raise ToolError(f"Ошибка расчета рыночной тональности: {error}") from error

    threshold = datetime.now(timezone.utc) - timedelta(days=7)
    filtered = []
    for row in rows:
        try:
            published = _parse_iso_datetime(str(row.get("published", "")))
        except Exception:
            continue
        if published >= threshold:
            filtered.append(row)

    positive = sum(1 for row in filtered if row.get("sentiment") == "positive")
    negative = sum(1 for row in filtered if row.get("sentiment") == "negative")
    neutral = sum(1 for row in filtered if row.get("sentiment") == "neutral")
    total = positive + negative + neutral
    score = compute_sentiment_score(positive, negative, total)
    top_headlines = [row.get("title", "") for row in filtered[:5]]

    return {
        "ticker": normalized_ticker,
        "positive": positive,
        "negative": negative,
        "neutral": neutral,
        "score": round(score, 6),
        "top_headlines": top_headlines,
    }


@mcp.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
async def get_macro_calendar(date_from: str, date_to: str) -> list[dict[str, Any]]:
    """Возвращает календарь макро-событий ЦБ РФ в заданном диапазоне."""
    log.info("mcp_tool_called", tool="get_macro_calendar", date_from=date_from, date_to=date_to)
    start = _validate_date(date_from, "date_from")
    end = _validate_date(date_to, "date_to")
    if end < start:
        raise ToolError("date_to не может быть меньше date_from.")

    payload = []
    for event in MACRO_CALENDAR:
        event_date = _validate_date(event["date"], "macro_event_date")
        if start <= event_date <= end:
            payload.append(event)
    return payload


if __name__ == "__main__":
    mcp.run(show_banner=False)

