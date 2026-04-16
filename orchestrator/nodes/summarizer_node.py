"""Узел финальной суммаризации ответа для пользователя."""

from __future__ import annotations

from collections.abc import Iterable
import json
from typing import Any

from langchain_core.messages import AIMessage
import structlog

log = structlog.get_logger()


def _escape_untrusted_text(value: str) -> str:
    """Экранирует потенциально опасные символы из недоверенного контента."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


def _sanitize_news_rows(news_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Санитизирует текстовые поля новостей перед использованием в промптах/ответах."""
    sanitized_rows: list[dict[str, Any]] = []
    for row in news_data:
        if not isinstance(row, dict):
            continue
        sanitized_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str):
                sanitized_row[key] = _escape_untrusted_text(value)
            else:
                sanitized_row[key] = value
        sanitized_rows.append(sanitized_row)
    return sanitized_rows


def _decode_json_payload(value: Any) -> Any:
    """Пытается декодировать JSON-строку в объект Python."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _find_payload_dict(value: Any) -> dict[str, Any] | None:
    """Извлекает вложенный словарь из произвольной структуры payload."""
    decoded = _decode_json_payload(value)
    if isinstance(decoded, dict):
        if "text" in decoded:
            text_decoded = _decode_json_payload(decoded["text"])
            if isinstance(text_decoded, dict):
                return text_decoded
            if isinstance(text_decoded, list):
                for item in text_decoded:
                    result = _find_payload_dict(item)
                    if result is not None:
                        return result
        if "content" in decoded and isinstance(decoded["content"], list):
            for item in decoded["content"]:
                result = _find_payload_dict(item)
                if result is not None:
                    return result
        if "payload" in decoded:
            result = _find_payload_dict(decoded["payload"])
            if result is not None:
                return result
        return decoded
    if isinstance(decoded, list):
        for item in decoded:
            result = _find_payload_dict(item)
            if result is not None:
                return result
    if hasattr(decoded, "text"):
        return _find_payload_dict(getattr(decoded, "text"))
    return None


def _find_payload_list(value: Any) -> list[dict[str, Any]]:
    """Извлекает список словарей из payload разной структуры."""
    decoded = _decode_json_payload(value)
    rows: list[dict[str, Any]] = []
    if isinstance(decoded, dict):
        nested = _find_payload_dict(decoded)
        if nested is not None:
            rows.append(nested)
        return rows
    if isinstance(decoded, list):
        for item in decoded:
            nested = _find_payload_dict(item)
            if nested is not None:
                rows.append(nested)
        return rows
    if hasattr(decoded, "text"):
        return _find_payload_list(getattr(decoded, "text"))
    return rows


def _as_float(value: Any) -> float | None:
    """Пытается привести значение к float."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def _first_numeric(value: Any) -> float | None:
    """Находит первое числовое значение в структуре данных."""
    direct = _as_float(value)
    if direct is not None:
        return direct
    if isinstance(value, dict):
        preferred_keys = ("value", "amount", "value_annual", "pct", "percent")
        for key in preferred_keys:
            if key in value:
                candidate = _first_numeric(value[key])
                if candidate is not None:
                    return candidate
        for nested_value in value.values():
            candidate = _first_numeric(nested_value)
            if candidate is not None:
                return candidate
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        for nested_value in value:
            candidate = _first_numeric(nested_value)
            if candidate is not None:
                return candidate
    return None


def _format_float(value: float | None, *, suffix: str = "", scale: float = 1.0) -> str:
    """Форматирует числовое значение для пользовательского ответа."""
    if value is None:
        return "н/д"
    normalized = value * scale
    return f"{normalized:,.2f}{suffix}".replace(",", " ")


def _extract_market_lines(market_data: dict[str, Any]) -> list[str]:
    """Возвращает ключевые рыночные факты для пользователя."""
    lines: list[str] = []
    quote = _find_payload_dict(market_data.get("get_stock_quote"))
    if isinstance(quote, dict):
        ticker = str(quote.get("SECID", "инструмент"))
        last_price = _format_float(_as_float(quote.get("LAST")), suffix=" RUB")
        change = _as_float(quote.get("CHANGE"))
        updated = str(quote.get("UPDATETIME", "н/д"))
        change_part = "н/д" if change is None else f"{change:+.2f}"
        lines.append(
            f"- Котировка {ticker}: {last_price}, изменение за сессию {change_part}, "
            f"время обновления {updated}."
        )

    index_data = _find_payload_dict(market_data.get("get_index_analytics"))
    if isinstance(index_data, dict):
        index_value = _format_float(_as_float(index_data.get("value")))
        index_change = _as_float(index_data.get("change"))
        change_text = "н/д" if index_change is None else f"{index_change:+.2f}%"
        lines.append(f"- Индекс: значение {index_value}, изменение {change_text}.")

    bond_data = _find_payload_dict(market_data.get("get_bond_data"))
    if isinstance(bond_data, dict):
        ticker = str(bond_data.get("SECID", "облигация"))
        ytm = _format_float(_as_float(bond_data.get("YIELDATPREVWAPRICE")), suffix="%")
        duration = _format_float(_as_float(bond_data.get("DURATION")))
        lines.append(f"- Параметры облигации {ticker}: доходность {ytm}, дюрация {duration}.")
    return lines


def _extract_news_lines(news_data: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    """Возвращает ключевые новости с источником и тональностью."""
    lines: list[str] = []
    news_rows: list[dict[str, Any]] = []
    for row in news_data:
        news_rows.extend(_find_payload_list(row))
    news_rows = [row for row in news_rows if "title" in row]
    for row in news_rows[:limit]:
        source = str(row.get("source", "unknown"))
        trust = str(row.get("source_trust", "n/a"))
        title = str(row.get("title", "без заголовка"))
        sentiment = str(row.get("sentiment", "neutral"))
        published = str(row.get("published", "н/д"))
        lines.append(
            f"- [{source}/{trust}] {title} (тональность: {sentiment}, дата: {published})."
        )

    sentiment_payloads = []
    for row in news_data:
        if isinstance(row, dict) and row.get("tool") == "get_market_sentiment":
            sentiment_payloads.append(_find_payload_dict(row.get("payload")))
    for payload in sentiment_payloads:
        if isinstance(payload, dict):
            ticker = str(payload.get("ticker", "инструмент"))
            score = _format_float(_as_float(payload.get("score")))
            lines.append(f"- Агрегированная тональность по {ticker}: score={score}.")
    return lines


def _extract_risk_lines(portfolio_metrics: dict[str, Any]) -> list[str]:
    """Возвращает риск-метрики и стресс-факты в читаемом виде."""
    lines: list[str] = []
    risk_payload = _find_payload_dict(portfolio_metrics.get("calculate_risk_metrics"))
    if isinstance(risk_payload, dict):
        var_hist = risk_payload.get("var_historical")
        if isinstance(var_hist, dict):
            lines.append(
                f"- VaR (ист.): {_format_float(_as_float(var_hist.get('value_pct')), suffix='%', scale=100.0)}."
            )
        var_param = risk_payload.get("var_parametric")
        if isinstance(var_param, dict):
            lines.append(
                f"- VaR (парам.): {_format_float(_as_float(var_param.get('value_pct')), suffix='%', scale=100.0)}."
            )
        cvar = risk_payload.get("cvar")
        if isinstance(cvar, dict):
            lines.append(f"- CVaR: {_format_float(_as_float(cvar.get('value_pct')), suffix='%', scale=100.0)}.")
        volatility = risk_payload.get("volatility")
        if isinstance(volatility, dict):
            lines.append(
                f"- Волатильность: {_format_float(_as_float(volatility.get('value_annual')), suffix='%', scale=100.0)}."
            )
        sharpe = risk_payload.get("sharpe")
        if isinstance(sharpe, dict):
            lines.append(f"- Коэффициент Шарпа: {_format_float(_as_float(sharpe.get('value')))}.")
        max_drawdown = risk_payload.get("max_drawdown")
        if isinstance(max_drawdown, dict):
            lines.append(
                f"- Max Drawdown: {_format_float(_as_float(max_drawdown.get('value')), suffix='%', scale=100.0)}."
            )
        hhi = risk_payload.get("hhi")
        if isinstance(hhi, dict):
            hhi_value = None
            if isinstance(hhi.get("positions"), dict):
                hhi_value = _as_float(hhi["positions"].get("value"))
            if hhi_value is None and isinstance(hhi.get("sectors"), dict):
                hhi_value = _as_float(hhi["sectors"].get("value"))
            lines.append(f"- HHI концентрации: {_format_float(hhi_value)}.")

    stress_payload = _find_payload_dict(portfolio_metrics.get("run_stress_test"))
    if isinstance(stress_payload, dict):
        scenario = str(stress_payload.get("scenario", "н/д"))
        magnitude = _format_float(_as_float(stress_payload.get("magnitude")), suffix="%")
        total_loss_rub = _format_float(_as_float(stress_payload.get("total_loss_rub")), suffix=" RUB")
        total_loss_pct = _format_float(_as_float(stress_payload.get("total_loss_pct")), suffix="%")
        var_comparison = str(stress_payload.get("current_var_comparison", "н/д"))
        lines.append(
            f"- Стресс-тест ({scenario}, масштаб {magnitude}): потери {total_loss_rub} "
            f"({total_loss_pct}), сравнение с VaR: {var_comparison}."
        )

    fallback_summary = _find_payload_dict(portfolio_metrics.get("fallback_portfolio_summary"))
    if isinstance(fallback_summary, dict):
        portfolio_id = str(fallback_summary.get("portfolio_id", "портфель"))
        total_value = _format_float(_as_float(fallback_summary.get("total_value")), suffix=" RUB")
        lines.append(
            f"- Риск-метрики недоступны, использована fallback-сводка для {portfolio_id}: "
            f"стоимость {total_value}."
        )
    return lines


def _build_interpretation(
    query_type: str,
    has_market: bool,
    has_news: bool,
    has_risk: bool,
) -> str:
    """Формирует интерпретацию результатов под тип запроса."""
    if query_type == "market_monitor":
        return (
            "Основной фокус — динамика текущей цены и краткосрочного движения инструмента. "
            "Эти данные подходят для оперативного мониторинга, но без контекста новостей и риска."
        )
    if query_type == "news_analysis":
        return (
            "Фокус на информационном фоне и качестве источников. "
            "Для принятия решений желательно сопоставить новости с реакцией цены."
        )
    if query_type == "risk_assessment":
        return (
            "Фокус на устойчивости портфеля к рыночным колебаниям и шоковым сценариям. "
            "Ключевыми являются величина потенциальных потерь и концентрация позиций."
        )
    if has_market and has_news and has_risk:
        return (
            "Запрос обработан как комплексный: объединены рыночные факты, новостной фон и риск-профиль. "
            "Такой срез даёт более сбалансированную картину по портфелю и инструментам."
        )
    return "Собранные данные частично покрывают запрос, интерпретацию нужно делать с учетом ограничений."


def _build_conclusion(query_type: str, warnings: list[str]) -> list[str]:
    """Формирует практический вывод под тип пользовательского запроса."""
    lines: list[str] = []
    if query_type == "market_monitor":
        lines.append("- Следите за внутридневной динамикой цены, изменением и временем обновления котировки.")
    elif query_type == "news_analysis":
        lines.append("- Приоритизируйте источники HIGH trust и проверяйте, как новости отражаются в цене.")
    elif query_type == "risk_assessment":
        lines.append("- Контролируйте лимиты VaR/CVaR и концентрацию портфеля (HHI) в рамках риск-бюджета.")
    else:
        lines.append("- Сопоставляйте рыночные данные, новости и риск-метрики перед изменением структуры портфеля.")

    if warnings:
        lines.append("- Учитывайте ограничения данных в этом ответе; при возможности повторите запрос позже.")
    else:
        lines.append("- Данные можно использовать как аналитический ориентир, но не как инвестиционную рекомендацию.")
    return lines


def _format_summary(state: dict[str, Any]) -> str:
    """Формирует структурированный итоговый ответ без внешних вызовов."""
    user_query = str(state.get("user_query", "")).strip() or "Запрос не указан."
    query_type = str(state.get("query_type", "complex"))
    market_data = dict(state.get("market_data", {}))
    news_data = _sanitize_news_rows(list(state.get("news_data", [])))
    portfolio_metrics = dict(state.get("portfolio_metrics", {}))
    warnings = list(state.get("warnings", []))

    market_lines = _extract_market_lines(market_data)
    news_lines = _extract_news_lines(news_data)
    risk_lines = _extract_risk_lines(portfolio_metrics)

    has_market = bool(market_lines)
    has_news = bool(news_lines)
    has_risk = bool(risk_lines)
    interpretation = _build_interpretation(query_type, has_market, has_news, has_risk)
    conclusion_lines = _build_conclusion(query_type, warnings)

    lines = ["## Итоговый анализ", "", "### Что запросил пользователь", f"- {user_query}", "", "### Ключевые данные"]
    if has_market:
        lines.extend(market_lines)
    if has_news:
        lines.extend(news_lines)
    if has_risk:
        lines.extend(risk_lines)
    if not (has_market or has_news or has_risk):
        lines.append("- Недостаточно данных для содержательного ответа по запросу.")

    lines.extend(["", "### Интерпретация", interpretation, "", "### Ограничения данных"])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- Существенных ограничений по доступности данных не зафиксировано.")

    lines.extend(["", "### Практический вывод"])
    lines.extend(conclusion_lines)
    return "\n".join(lines)


async def summarizer_node(state: dict[str, Any]) -> dict[str, Any]:
    """Суммаризирует накопленные данные и формирует final_answer."""
    log.info(
        "summarizer_node_started",
        query_type=state.get("query_type"),
        market_keys=sorted(list(dict(state.get("market_data", {})).keys())),
        news_rows=len(list(state.get("news_data", []))),
        portfolio_keys=sorted(list(dict(state.get("portfolio_metrics", {})).keys())),
        warnings_count=len(list(state.get("warnings", []))),
    )
    final_answer = _format_summary(state)
    log.info("summarizer_node_completed", final_answer_length=len(final_answer))
    return {
        "final_answer": final_answer,
        "messages": [AIMessage(content=final_answer)],
        "next_node": "summarizer",
    }

