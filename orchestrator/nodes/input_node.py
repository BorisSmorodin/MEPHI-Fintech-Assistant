"""Узел валидации и первичной классификации пользовательского запроса."""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import HumanMessage

from orchestrator.state import QueryType

TICKER_PATTERN = re.compile(r"\b[A-Z0-9]{3,12}\b")

MARKET_KEYWORDS = {"котиров", "объём", "объем", "торг", "курс", "imoex", "rtsi", "тикер"}
NEWS_KEYWORDS = {"новост", "событ", "объявл", "ставк", "цб", "влияни"}
RISK_KEYWORDS = {"риск", "портфел", "var", "просад", "диверсификац", "стресс"}


def _classify_query_type(query: str) -> QueryType:
    """Классифицирует тип запроса по ключевым словам."""
    lowered = query.lower()
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

    extracted_tickers = sorted(set(TICKER_PATTERN.findall(user_query.upper())))
    query_type = _classify_query_type(user_query)
    return {
        "user_query": user_query,
        "query_type": query_type,
        "extracted_tickers": extracted_tickers,
        "messages": [HumanMessage(content=user_query)],
    }

