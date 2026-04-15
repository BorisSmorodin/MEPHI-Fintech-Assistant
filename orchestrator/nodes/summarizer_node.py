"""Узел финальной суммаризации ответа для пользователя."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage


def _format_summary(state: dict[str, Any]) -> str:
    """Формирует структурированный итоговый ответ без внешних вызовов."""
    market_data = dict(state.get("market_data", {}))
    news_data = list(state.get("news_data", []))
    portfolio_metrics = dict(state.get("portfolio_metrics", {}))
    warnings = list(state.get("warnings", []))

    lines = ["## Итоговый анализ", "", "### Ключевые факты"]
    if market_data:
        lines.append(f"- Получены рыночные данные: {', '.join(sorted(market_data.keys()))}.")
    else:
        lines.append("- Рыночные данные отсутствуют.")

    if news_data:
        high_trust_count = sum(1 for row in news_data if str(row.get("source_trust", "")).upper() == "HIGH")
        lines.append(f"- Получено новостей: {len(news_data)} (HIGH trust: {high_trust_count}).")
    else:
        lines.append("- Новостные данные отсутствуют.")

    if portfolio_metrics:
        lines.append(f"- Получены аналитические метрики: {', '.join(sorted(portfolio_metrics.keys()))}.")
    else:
        lines.append("- Аналитические метрики отсутствуют.")

    lines.extend(["", "### Риски"])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- Критических предупреждений не зафиксировано.")

    lines.extend(["", "### Итог", "Рекомендация: используйте вывод как аналитический ориентир, не как инвестсовет."])
    return "\n".join(lines)


async def summarizer_node(state: dict[str, Any]) -> dict[str, Any]:
    """Суммаризирует накопленные данные и формирует final_answer."""
    final_answer = _format_summary(state)
    return {
        "final_answer": final_answer,
        "messages": [AIMessage(content=final_answer)],
        "next_node": "summarizer",
    }

