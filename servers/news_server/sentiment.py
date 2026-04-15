"""Эвристическая оценка тональности новостей."""

from __future__ import annotations

from typing import Literal

SentimentLabel = Literal["positive", "negative", "neutral"]

POSITIVE_MARKERS = {
    "рост",
    "прибыль",
    "дивиденд",
    "укрепление",
    "повышение",
    "позитив",
    "buyback",
    "upgrade",
    "beat",
    "surge",
}

NEGATIVE_MARKERS = {
    "падение",
    "убыток",
    "санкц",
    "снижение",
    "дефолт",
    "негатив",
    "downgrade",
    "default",
    "drop",
    "loss",
}


def classify_sentiment(text: str) -> SentimentLabel:
    """Классифицирует тональность новости по словарям маркеров."""
    normalized = text.lower().strip()
    if not normalized:
        return "neutral"

    positive_hits = sum(1 for marker in POSITIVE_MARKERS if marker in normalized)
    negative_hits = sum(1 for marker in NEGATIVE_MARKERS if marker in normalized)
    if positive_hits > negative_hits:
        return "positive"
    if negative_hits > positive_hits:
        return "negative"
    return "neutral"


def compute_sentiment_score(positive: int, negative: int, total: int) -> float:
    """Вычисляет агрегированный sentiment score в диапазоне [-1, +1]."""
    if total <= 0:
        return 0.0
    score = (positive - negative) / total
    if score > 1.0:
        return 1.0
    if score < -1.0:
        return -1.0
    return float(score)

