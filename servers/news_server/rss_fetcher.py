"""Модуль агрегирования RSS-источников."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from typing import Any, Callable

import feedparser
import requests
import structlog

from config.settings import Settings, get_settings

log = structlog.get_logger()

NEWS_SOURCES: dict[str, dict[str, str]] = {
    "cbr": {"name": "Банк России", "url": "https://www.cbr.ru/rss/RssFeed/", "trust": "HIGH"},
    "interfax": {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "trust": "HIGH"},
    "tass": {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "trust": "HIGH"},
    "rbc": {"name": "РБК", "url": "https://rbc.ru/v10/rss/v1", "trust": "MEDIUM"},
    "smartlab": {"name": "Smart-Lab", "url": "https://smart-lab.ru/rss.xml", "trust": "LOW"},
    "cbonds": {"name": "Cbonds", "url": "https://cbonds.ru/rss/", "trust": "MEDIUM"},
}


@dataclass(slots=True)
class TTLCache:
    """Простой in-memory TTL-кэш для RSS-лент."""

    time_provider: Callable[[], float] = time.time
    _storage: dict[str, tuple[float, Any]] = field(default_factory=dict)

    def get(self, key: str) -> Any | None:
        """Возвращает объект из кэша при валидном TTL."""
        now = self.time_provider()
        if key not in self._storage:
            return None
        expires_at, payload = self._storage[key]
        if now >= expires_at:
            del self._storage[key]
            return None
        return payload

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        """Сохраняет объект в кэш."""
        self._storage[key] = (self.time_provider() + ttl_seconds, value)


class NewsFetchError(Exception):
    """Ошибка агрегации новостей."""


def _parse_datetime(value: Any) -> datetime:
    """Нормализует дату публикации RSS записи."""
    if isinstance(value, datetime):
        dt_value = value
    else:
        try:
            dt_value = parsedate_to_datetime(str(value))
        except Exception:
            dt_value = datetime.now(timezone.utc)
    if dt_value.tzinfo is None:
        return dt_value.replace(tzinfo=timezone.utc)
    return dt_value.astimezone(timezone.utc)


def _normalize_text(value: Any) -> str:
    """Приводит значение к нормализованной строке."""
    return str(value or "").strip()


@dataclass(slots=True)
class NewsFetcher:
    """Агрегатор RSS-источников с кэшем и отказоустойчивостью."""

    settings: Settings
    session: requests.Session = field(default_factory=requests.Session)
    time_provider: Callable[[], float] = time.time
    _cache: TTLCache = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Инициализирует кэш."""
        self._cache = TTLCache(time_provider=self.time_provider)

    def _fetch_source_entries(self, source_key: str) -> list[dict]:
        """Загружает и парсит одну RSS-ленту."""
        source = NEWS_SOURCES[source_key]
        cache_key = f"rss:{source_key}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info("rss_cache_hit", source=source_key)
            return cached

        log.info("rss_cache_miss", source=source_key)
        url = source["url"]
        try:
            response = self.session.get(url, timeout=self.settings.news_http_timeout_sec)
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
        except Exception as error:
            raise NewsFetchError(f"Источник {source_key} недоступен: {error}") from error

        entries: list[dict] = []
        for item in parsed.entries:
            published_raw = _normalize_text(
                getattr(item, "published", "")
                or getattr(item, "updated", "")
                or getattr(item, "pubDate", "")
            )
            published_at = _parse_datetime(published_raw)
            entries.append(
                {
                    "title": _normalize_text(getattr(item, "title", "")),
                    "url": _normalize_text(getattr(item, "link", "")),
                    "published": published_at.isoformat(),
                    "published_dt": published_at,
                    "source": source_key,
                    "source_name": source["name"],
                    "source_trust": source["trust"],
                    "summary": _normalize_text(getattr(item, "summary", "")),
                }
            )

        self._cache.set(cache_key, entries, self.settings.news_server_cache_ttl)
        return entries

    def fetch_news(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Возвращает отфильтрованные и отсортированные новости."""
        if limit <= 0:
            raise NewsFetchError("Параметр limit должен быть положительным.")
        if limit > 100:
            raise NewsFetchError("Параметр limit не должен превышать 100.")

        selected_sources = sources or list(NEWS_SOURCES.keys())
        normalized_sources = [source.strip().lower() for source in selected_sources]
        unknown_sources = [source for source in normalized_sources if source not in NEWS_SOURCES]
        if unknown_sources:
            raise NewsFetchError(f"Неизвестные источники: {unknown_sources}")

        query_terms = [token for token in query.lower().strip().split() if token]
        rows: list[dict] = []
        for source_key in normalized_sources:
            try:
                rows.extend(self._fetch_source_entries(source_key))
            except NewsFetchError as error:
                log.warning("rss_source_unavailable", source=source_key, error=str(error))

        deduplicated: dict[tuple[str, str], dict] = {}
        for row in rows:
            composite_text = f"{row['title']} {row['summary']}".lower()
            if query_terms and not all(token in composite_text for token in query_terms):
                continue
            dedup_key = (row["title"].lower(), row["source"])
            if dedup_key not in deduplicated:
                deduplicated[dedup_key] = row

        sorted_rows = sorted(
            deduplicated.values(),
            key=lambda item: item["published_dt"],
            reverse=True,
        )
        payload = []
        for row in sorted_rows[:limit]:
            payload.append(
                {
                    "title": row["title"],
                    "url": row["url"],
                    "published": row["published"],
                    "source": row["source"],
                    "source_trust": row["source_trust"],
                    "summary": row["summary"],
                }
            )
        return payload


def get_news_fetcher(
    settings: Settings | None = None,
    session: requests.Session | None = None,
    time_provider: Callable[[], float] | None = None,
) -> NewsFetcher:
    """Возвращает сконфигурированный NewsFetcher."""
    current_settings = settings or get_settings()
    return NewsFetcher(
        settings=current_settings,
        session=session or requests.Session(),
        time_provider=time_provider or time.time,
    )

