"""Узел валидации и первичной классификации пользовательского запроса."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage
import structlog

from orchestrator.state import QueryType

TICKER_PATTERN = re.compile(r"\b[A-Z0-9]{3,12}\b")
PERCENT_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?\s*%")
VAR_WORD_PATTERN = re.compile(r"\bvar\b", re.IGNORECASE)

MARKET_KEYWORDS = {"котиров", "объём", "объем", "торг", "курс", "imoex", "rtsi", "тикер"}
NEWS_KEYWORDS = {"новост", "событ", "объявл", "ставк", "цб", "влияни"}
RISK_KEYWORDS = {"риск", "портфел", "просад", "диверсификац", "стресс"}
_INDEX_KEYWORDS = {"imoex", "rtsi", "rgbi", "индекс", "moex"}
_SECTOR_STRESS_HINTS: tuple[tuple[str, str], ...] = (
    ("финанс", "финансы"),
    ("банков", "финансы"),
    ("нефтегаз", "нефтегаз"),
    ("нефт", "нефтегаз"),
    ("газ", "нефтегаз"),
    ("металлург", "металлургия"),
    ("металл", "металлургия"),
    ("ритейл", "ритейл"),
    ("рознич", "ритейл"),
    ("it", "it"),
    ("технолог", "it"),
    ("энергет", "энергетика"),
    ("телеком", "телеком"),
)
_STRESS_INTENT_PATTERNS = (
    re.compile(r"\bстресс(?:-?тест)?\b", re.IGNORECASE),
    re.compile(r"\bstress(?:-?test)?\b", re.IGNORECASE),
    re.compile(r"что\s+будет\s*,?\s*если", re.IGNORECASE),
    re.compile(r"сценар(?:ий|ия)\s+(?:падени|снижен|просад)", re.IGNORECASE),
    re.compile(r"(просяд|просед|упад|сниз|обвал)\w*\s+на\s*-?\d+(?:[.,]\d+)?\s*%", re.IGNORECASE),
)
_DROP_WORD_PATTERNS = (
    re.compile(r"\b(просяд\w*|просед\w*|упад\w*|падени\w*|снижен\w*|обвал\w*)\b", re.IGNORECASE),
    re.compile(r"-\s*\d+(?:[.,]\d+)?\s*%", re.IGNORECASE),
)
# Контекст портфеля без импорта planner_node (избегаем циклических зависимостей).
_PORTFOLIO_WORD_RE = re.compile(r"\b[\w-]*portfolio[\w-]*\b", re.IGNORECASE)
_PORTFOLIO_ID_RE = re.compile(r"\b[\w]+_portfolio\b", re.IGNORECASE)
# Намерение «состав / позиции» (до обобщающего «портфел» → risk_assessment).
_PORTFOLIO_COMPOSITION_HINTS = (
    "состав",
    "позици",
    "holdings",
    "какие бумаги",
    "что в портфел",
    "перечисли актив",
    "доли по бумагам",
    "что у меня в портфел",
    "бумаг в портфел",
)
# Явный запрос риск-метрик одновременно с составом → complex.
_PORTFOLIO_RISK_FOCUS_HINTS = (
    "риск",
    "стресс",
    "cvar",
    "волатильн",
    "шарп",
    "просадк",
)
_INVESTMENT_DECISION_PATTERNS = (
    re.compile(r"стоит\s+ли\s+(купить|продавать|покупать|продать)", re.IGNORECASE),
    re.compile(r"(купить|продать|покупать|продавать)\s+ли\b", re.IGNORECASE),
    re.compile(r"имеет\s+ли\s+смысл\s+(купить|продавать|покупать|продать)", re.IGNORECASE),
    re.compile(r"should\s+i\s+(buy|sell)", re.IGNORECASE),
    re.compile(r"\b(buy|sell)\s+or\s+(hold|not)\b", re.IGNORECASE),
)

_EXPLICIT_MARKET_HISTORY_HINTS = (
    "свеч",
    "график",
    "динамик",
    "истори",
    "тренд",
    "ohlc",
    "за недел",
    "за месяц",
    "за год",
    "за квартал",
    "просадк",
    "волатильност",
)

COMPANY_TICKER_HINTS: dict[str, str] = {
    "сбер": "SBER",
    "сбербанк": "SBER",
    "газпром": "GAZP",
    "лукойл": "LKOH",
    "роснефть": "ROSN",
    "норникель": "GMKN",
    "яндекс": "YDEX",
}
log = structlog.get_logger()


def is_investment_decision_intent(query: str) -> bool:
    """Определяет запрос о целесообразности покупки/продажи (без рекомендации в ответе)."""
    if any(pattern.search(query) for pattern in _INVESTMENT_DECISION_PATTERNS):
        return True
    lowered = query.lower()
    if "лучше купить" in lowered or "лучше продать" in lowered:
        return True
    return False


def is_explicit_market_history_intent(query: str) -> bool:
    """Явный запрос истории цен/свечей (тогда get_candles уместен)."""
    lowered = query.lower()
    return any(hint in lowered for hint in _EXPLICIT_MARKET_HISTORY_HINTS)


def infer_sector_stress_hint(query: str) -> str | None:
    """Извлекает отраслевую подсказку из пользовательского запроса для sector_decline."""
    lowered = query.casefold()
    for raw_hint, canonical in _SECTOR_STRESS_HINTS:
        if raw_hint in lowered:
            return canonical
    return None


def is_stress_intent(query: str) -> bool:
    """Определяет стресс-сценарный запрос по совокупности условий и контекста."""
    lowered = query.casefold()
    has_percent = bool(PERCENT_PATTERN.search(lowered))
    has_stress_phrase = any(pattern.search(query) for pattern in _STRESS_INTENT_PATTERNS)
    has_drop_phrase = any(pattern.search(query) for pattern in _DROP_WORD_PATTERNS)
    has_index_reference = any(keyword in lowered for keyword in _INDEX_KEYWORDS)
    has_sector_hint = infer_sector_stress_hint(query) is not None

    if has_stress_phrase:
        return True
    if has_percent and has_index_reference and has_drop_phrase:
        return True
    if has_percent and has_sector_hint and has_drop_phrase:
        return True
    return False


def _classify_query_type(query: str) -> QueryType:
    """Классифицирует тип запроса по ключевым словам."""
    lowered = query.lower()
    if is_stress_intent(query):
        return "risk_assessment"

    has_portfolio_context = (
        "портфел" in lowered
        or _PORTFOLIO_WORD_RE.search(query) is not None
        or _PORTFOLIO_ID_RE.search(query) is not None
    )
    has_composition_intent = any(hint in lowered for hint in _PORTFOLIO_COMPOSITION_HINTS)
    has_risk_focus = any(hint in lowered for hint in _PORTFOLIO_RISK_FOCUS_HINTS) or bool(VAR_WORD_PATTERN.search(query))
    if has_portfolio_context and has_composition_intent:
        if has_risk_focus:
            return "complex"
        return "portfolio_holdings"

    matches = 0
    query_type: QueryType = "complex"
    if any(keyword in lowered for keyword in MARKET_KEYWORDS):
        matches += 1
        query_type = "market_monitor"
    if any(keyword in lowered for keyword in NEWS_KEYWORDS):
        matches += 1
        query_type = "news_analysis"
    if any(keyword in lowered for keyword in RISK_KEYWORDS) or bool(VAR_WORD_PATTERN.search(query)):
        matches += 1
        query_type = "risk_assessment"
    if matches >= 2:
        return "complex"
    return query_type


async def input_node(state: dict[str, Any]) -> dict[str, Any]:
    """Валидирует запрос пользователя и подготавливает первичное состояние."""
    user_query = str(state.get("user_query", "")).strip()
    if not user_query:
        raise ValueError("Запрос пользователя не может быть пустым.")
    if len(user_query) > 2000:
        raise ValueError("Запрос пользователя не должен превышать 2000 символов.")

    extracted_tickers = set(TICKER_PATTERN.findall(user_query.upper()))
    lowered_query = user_query.lower()
    for hint, ticker in COMPANY_TICKER_HINTS.items():
        if hint in lowered_query:
            extracted_tickers.add(ticker)
    normalized_tickers = sorted(extracted_tickers)
    query_type = _classify_query_type(user_query)
    investment_decision = is_investment_decision_intent(user_query)
    log.info(
        "input_node_classified",
        query_type=query_type,
        extracted_tickers=normalized_tickers,
        query_length=len(user_query),
        investment_decision_intent=investment_decision,
    )
    return {
        "user_query": user_query,
        "query_type": query_type,
        "extracted_tickers": normalized_tickers,
        "investment_decision_intent": investment_decision,
        "messages": [HumanMessage(content=user_query)],
    }

