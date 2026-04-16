"""Узел валидации и первичной классификации пользовательского запроса."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage
import structlog

from orchestrator.state import QueryType

TICKER_PATTERN = re.compile(r"\b[A-Z0-9]{3,12}\b")
PERCENT_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?\s*%")

MARKET_KEYWORDS = {"котиров", "объём", "объем", "торг", "курс", "imoex", "rtsi", "тикер"}
NEWS_KEYWORDS = {"новост", "событ", "объявл", "ставк", "цб", "влияни"}
RISK_KEYWORDS = {"риск", "портфел", "var", "просад", "диверсификац", "стресс"}
STRESS_KEYWORDS = {"стресс", "stress", "сценар", "шок", "паден"}
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


def _classify_query_type(query: str) -> QueryType:
    """Классифицирует тип запроса по ключевым словам."""
    lowered = query.lower()
    has_percentage = bool(PERCENT_PATTERN.search(lowered))
    has_index_reference = any(keyword in lowered for keyword in {"imoex", "rtsi", "rgbi", "индекс"})
    if any(keyword in lowered for keyword in STRESS_KEYWORDS):
        return "risk_assessment"
    if has_percentage and has_index_reference:
        return "risk_assessment"

    has_portfolio_context = (
        "портфел" in lowered
        or _PORTFOLIO_WORD_RE.search(query) is not None
        or _PORTFOLIO_ID_RE.search(query) is not None
    )
    has_composition_intent = any(hint in lowered for hint in _PORTFOLIO_COMPOSITION_HINTS)
    has_risk_focus = any(hint in lowered for hint in _PORTFOLIO_RISK_FOCUS_HINTS) or bool(
        re.search(r"\bvar\b", lowered)
    )
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
    if any(keyword in lowered for keyword in RISK_KEYWORDS):
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

