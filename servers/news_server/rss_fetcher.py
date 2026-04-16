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
    "cbr": {"name": "Банк России", "url": "https://www.cbr.ru/rss/eventrss", "trust": "HIGH"},
    "interfax": {"name": "Интерфакс", "url": "https://www.interfax.ru/rss.asp", "trust": "HIGH"},
    "tass": {"name": "ТАСС", "url": "https://tass.ru/rss/v2.xml", "trust": "HIGH"},
    "rbc": {"name": "РБК", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss", "trust": "MEDIUM"},
    "smartlab": {"name": "Smart-Lab", "url": "https://smart-lab.ru/rss/", "trust": "LOW"},
    "finam": {"name": "Финам", "url": "https://www.finam.ru/net/analysis/conews/rsspoint", "trust": "MEDIUM"},
}
SOURCE_ALIASES = {"cbonds": "finam", "smart-lab": "smartlab"}

TICKER_ALIASES: dict[str, list[str]] = {
    "SBER": ["sber", "sberbank", "сбер", "сбербанк"],
    "GAZP": ["gazp", "gazprom", "газпром", "газпрома"],
    "LKOH": ["lkoh", "lukoil", "лукойл", "лукойла"],
    "ROSN": ["rosn", "rosneft", "роснефть", "роснефти"],
    "GMKN": ["gmkn", "nornickel", "норникель", "никель"],
    "YDEX": ["ydex", "yandex", "яндекс"],
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


def _expanded_query_terms(query: str) -> list[str]:
    """Расширяет термы запроса с учетом известных алиасов тикеров/компаний."""
    query_terms = [token for token in query.lower().strip().split() if token]
    expanded = list(query_terms)
    for token in query_terms:
        aliases = TICKER_ALIASES.get(token.upper())
        if aliases:
            expanded.extend(aliases)
    # Сохраняем порядок и убираем дубликаты.
    unique: list[str] = []
    for token in expanded:
        if token not in unique:
            unique.append(token)
    return unique


@dataclass(slots=True)
class NewsFetcher:
    """Агрегатор RSS-источников с кэшем и отказоустойчивостью."""

    settings: Settings
    session: requests.Session = field(default_factory=requests.Session)
    time_provider: Callable[[], float] = time.time
    _cache: TTLCache = field(init=False, repr=False)
    _source_health: dict[str, bool] = field(default_factory=dict, init=False, repr=False)
    _last_fetch_diagnostics: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Инициализирует кэш."""
        self._cache = TTLCache(time_provider=self.time_provider)
        self._source_health = {}
        self._last_fetch_diagnostics = {
            "unavailable_sources": [],
            "degraded": False,
            "strict_match_count": 0,
            "fallback_mode": "none",
        }

    @property
    def last_fetch_diagnostics(self) -> dict[str, Any]:
        """Возвращает диагностику последнего вызова fetch_news."""
        return dict(self._last_fetch_diagnostics)

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
        self._source_health[source_key] = True
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
        normalized_sources = []
        for source in selected_sources:
            normalized_source = source.strip().lower()
            normalized_source = SOURCE_ALIASES.get(normalized_source, normalized_source)
            normalized_sources.append(normalized_source)
        unknown_sources = [source for source in normalized_sources if source not in NEWS_SOURCES]
        if unknown_sources:
            raise NewsFetchError(f"Неизвестные источники: {unknown_sources}")

        query_terms = _expanded_query_terms(query)
        rows: list[dict] = []
        source_errors = 0
        unavailable_sources: list[str] = []
        for source_key in normalized_sources:
            try:
                rows.extend(self._fetch_source_entries(source_key))
            except NewsFetchError as error:
                log.warning("rss_source_unavailable", source=source_key, error=str(error))
                source_errors += 1
                unavailable_sources.append(source_key)
                self._source_health[source_key] = False

        deduplicated: dict[tuple[str, str], dict] = {}
        strict_match_count = 0
        for row in rows:
            composite_text = f"{row['title']} {row['summary']}".lower()
            if query_terms and not all(token in composite_text for token in query_terms):
                continue
            strict_match_count += 1
            dedup_key = (row["title"].lower(), row["source"])
            if dedup_key not in deduplicated:
                deduplicated[dedup_key] = row

        # Если strict all-terms ничего не дал, пробуем мягкий OR-поиск.
        fallback_mode = "none"
        if query_terms and not deduplicated:
            fallback_mode = "or_query_terms"
            for row in rows:
                composite_text = f"{row['title']} {row['summary']}".lower()
                if any(token in composite_text for token in query_terms):
                    dedup_key = (row["title"].lower(), row["source"])
                    if dedup_key not in deduplicated:
                        deduplicated[dedup_key] = row

        if query_terms and not deduplicated and rows:
            fallback_mode = "recent_topn"
            for row in rows:
                dedup_key = (row["title"].lower(), row["source"])
                if dedup_key not in deduplicated:
                    deduplicated[dedup_key] = row

        sorted_rows = sorted(
            deduplicated.values(),
            key=lambda item: item["published_dt"],
            reverse=True,
        )
        degraded = bool(normalized_sources) and (source_errors / len(normalized_sources)) > 0.5
        self._last_fetch_diagnostics = {
            "unavailable_sources": unavailable_sources,
            "degraded": degraded,
            "strict_match_count": strict_match_count,
            "fallback_mode": fallback_mode,
            "source_errors": source_errors,
            "sources_total": len(normalized_sources),
            "result_count": min(len(sorted_rows), limit),
        }
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

