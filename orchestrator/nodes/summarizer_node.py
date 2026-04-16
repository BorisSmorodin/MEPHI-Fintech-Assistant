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
        source = _escape_untrusted_text(str(row.get("source", "unknown")))
        trust = _escape_untrusted_text(str(row.get("source_trust", "n/a")))
        title = _escape_untrusted_text(str(row.get("title", "без заголовка")))
        sentiment = _escape_untrusted_text(str(row.get("sentiment", "neutral")))
        published = _escape_untrusted_text(str(row.get("published", "н/д")))
        lines.append(
            f"- [{source}/{trust}] {title} (тональность: {sentiment}, дата: {published})."
        )

    sentiment_payloads = []
    for row in news_data:
        if isinstance(row, dict) and row.get("tool") == "get_market_sentiment":
            sentiment_payloads.append(_find_payload_dict(row.get("payload")))
    for payload in sentiment_payloads:
        if isinstance(payload, dict):
            ticker = _escape_untrusted_text(str(payload.get("ticker", "инструмент")))
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
        mag_val = _as_float(stress_payload.get("magnitude"))
        if scenario == "index_drop":
            mag_label = (
                f"падение рыночного прокси на {_format_float(mag_val, suffix=' п.п.')}"
                if mag_val is not None
                else "масштаб: н/д"
            )
        elif scenario == "rate_hike":
            mag_label = (
                f"рост ставки на {_format_float(mag_val, suffix=' п.п.')}"
                if mag_val is not None
                else "масштаб: н/д"
            )
        elif scenario == "sector_decline":
            mag_label = (
                f"просадка сектора на {_format_float(mag_val, suffix=' п.п.')}"
                if mag_val is not None
                else "масштаб: н/д"
            )
        else:
            mag_label = f"масштаб {_format_float(mag_val, suffix=' п.п.')}" if mag_val is not None else "масштаб: н/д"
        total_loss_rub = _format_float(_as_float(stress_payload.get("total_loss_rub")), suffix=" RUB")
        total_loss_pct = _format_float(_as_float(stress_payload.get("total_loss_pct")), suffix="%", scale=100.0)
        nav = _format_float(_as_float(stress_payload.get("portfolio_value_rub")), suffix=" RUB")
        aff = stress_payload.get("affected_positions_count")
        aff_part = f", затронуто позиций: {aff}" if aff is not None else ""
        var_comparison = str(stress_payload.get("current_var_comparison", "н/д"))
        lines.append(
            f"- Стресс-тест ({scenario}, {mag_label}; NAV ≈ {nav}{aff_part}): оценочные потери "
            f"{total_loss_rub} ({total_loss_pct} от NAV). Сравнение с VaR: {var_comparison}."
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


def _interpret_stress_block(stress: dict[str, Any]) -> str:
    """Интерпретация по результатам run_stress_test (упрощённая модель, не оферта)."""
    scenario = str(stress.get("scenario", "")).strip().lower()
    mag = _as_float(stress.get("magnitude"))
    tlp = _as_float(stress.get("total_loss_pct"))
    nav = _as_float(stress.get("portfolio_value_rub"))
    comparison = str(stress.get("current_var_comparison", "")).strip()
    n_aff_raw = stress.get("affected_positions_count")
    n_aff: int | None
    try:
        n_aff = int(float(n_aff_raw)) if n_aff_raw is not None else None
    except (TypeError, ValueError):
        n_aff = None

    mag_txt = _format_float(mag, suffix=" п.п.") if mag is not None else "н/д"
    loss_txt = _format_float(tlp, suffix="% от NAV", scale=100.0) if tlp is not None else "н/д"
    nav_txt = _format_float(nav, suffix=" RUB") if nav is not None else "н/д"

    scenario_ru = {
        "index_drop": "падение рыночного прокси (индекса)",
        "rate_hike": "рост ключевой ставки",
        "sector_decline": "просадка отраслевого сектора",
    }.get(scenario, scenario or "сценарий")

    parts: list[str] = [
        f"Сценарий «{scenario_ru}» задан интенсивностью около {mag_txt}. "
        f"По упрощённой beta-модели оценочная просадка портфеля порядка {loss_txt} "
        f"при оценочной стоимости портфеля {nav_txt}."
    ]
    if n_aff is not None and n_aff >= 0:
        parts.append(f" В расчёте участвует позиций: {n_aff}.")
    if comparison:
        parts.append(f" {comparison}")
        if "выше" in comparison.lower():
            parts.append(
                " На практике это сигнал, что заданный шок «тяжелее» типичного плохого дня по VaR(95%): "
                "стоит проверить лимиты и буфер ликвидности."
            )
        elif "ниже" in comparison.lower():
            parts.append(
                " Даже при заданном шоке оценочный ущерб остаётся скромнее текущего однодневного VaR(95%): "
                "риск по модели выглядит умеренным, но это всё ещё упрощение (без нелинейностей и ликвидности)."
            )
        else:
            parts.append(
                " Масштаб ущерба близок к текущему однодневному VaR(95%) — имеет смысл смотреть на концентрацию и чувствительность к рынку."
            )
    parts.append(
        " Модель не учитывает комиссии, проскальзывание и корреляционные сдвиги в стрессе — используйте вывод как ориентир."
    )
    return "".join(parts).strip()


def _interpret_risk_metrics_block(risk: dict[str, Any]) -> str:
    """Интерпретация по calculate_risk_metrics: связи между метриками, не дублирование таблицы цифр."""
    paragraphs: list[str] = []

    var_hist = risk.get("var_historical") if isinstance(risk.get("var_historical"), dict) else {}
    var_param = risk.get("var_parametric") if isinstance(risk.get("var_parametric"), dict) else {}
    cvar_block = risk.get("cvar") if isinstance(risk.get("cvar"), dict) else {}

    vh = _as_float(var_hist.get("value_pct")) if var_hist else None
    vp = _as_float(var_param.get("value_pct")) if var_param else None
    vc = _as_float(cvar_block.get("value_pct")) if cvar_block else None
    conf = _as_float(risk.get("confidence")) if risk.get("confidence") is not None else None
    conf_label = f"{conf:.0%}" if conf is not None and 0 < conf < 1 else "95%"

    # --- VaR / CVaR: смысл и взаимосвязи (цифры уже в «Ключевых данных»).
    if vh is not None or vp is not None or vc is not None:
        var_lines: list[str] = [
            f"Потери и хвост распределения (VaR / CVaR, доверие {conf_label}). "
            f"VaR отвечает на вопрос «какой однодневной убыток не должен превышаться в большинстве дней»; "
            f"CVaR описывает среднюю тяжесть дней ещё хуже этого порога — это риск «второго порядка», важный для лимитов и капитала."
        ]
        if vh is not None and vp is not None and max(vh, vp) > 1e-12:
            rel_diff = abs(vh - vp) / max(vh, vp)
            if rel_diff < 0.12:
                var_lines.append(
                    "Исторический и параметрический VaR близки по величине: упрощение нормальным распределением не противоречит "
                    "порядку оценок на доступной истории."
                )
            else:
                var_lines.append(
                    "Исторический и параметрический VaR заметно расходятся: реальные доходности могут иметь более тяжёлые хвосты или асимметрию, "
                    "чем предполагает параметрическая модель — полезно смотреть на обе оценки, а не на одну."
                )
        if vc is not None and vh is not None and vh > 1e-12:
            tail_ratio = vc / vh
            if tail_ratio > 1.2:
                var_lines.append(
                    "CVaR существенно выше VaR: в редких «плохих» днях средний ущерб заметно больше, чем граница VaR — "
                    "портфель чувствителен к экстремальным сценариям относительно выбранного квантиля."
                )
            elif tail_ratio > 1.05:
                var_lines.append(
                    "CVaR умеренно превышает VaR — типично для хвостов: при пробое порога убытки в среднем тяжелее, чем сам порог."
                )
            else:
                var_lines.append(
                    "CVaR близок к VaR: хвост на доступной истории не выглядит чрезмерно «толстым» относительно выбранного уровня доверия."
                )
        paragraphs.append(" ".join(var_lines))

    vol = risk.get("volatility")
    sharpe = risk.get("sharpe")
    if isinstance(vol, dict):
        va = _as_float(vol.get("value_annual"))
        vol_hint = vol.get("interpretation")
        vol_hint_s = str(vol_hint).strip() if isinstance(vol_hint, str) else ""
        sharpe_val = _as_float(sharpe.get("value")) if isinstance(sharpe, dict) else None
        sharpe_hint = sharpe.get("interpretation") if isinstance(sharpe, dict) else None
        sharpe_hint_s = str(sharpe_hint).strip() if isinstance(sharpe_hint, str) else ""

        vol_para: list[str] = ["Волатильность и доходность на единицу риска."]
        if vol_hint_s:
            vol_para.append(f"{vol_hint_s}")
        if va is not None:
            vol_para.append(
                "Годовая волатильность задаёт масштаб колебаний стоимости: чем она выше, тем шире диапазон типичных дневных движений "
                "и тем чувствительнее портфель к рыночным шокам при прочих равных."
            )
        if sharpe_val is not None:
            if sharpe_hint_s:
                vol_para.append(f"Коэффициент Шарпа: {sharpe_hint_s}")
            if sharpe_val < 0:
                vol_para.append(
                    "Отрицательный Шарп означает, что на горизонте оценки доходность не компенсировала принятый риск и безрисковую ставку — "
                    "имеет смысл пересмотреть состав, издержки и ожидания по доходности."
                )
            elif sharpe_val < 0.5:
                vol_para.append(
                    "Низкий положительный Шарп указывает на слабую «цену» риска: улучшение соотношения доходность/волатильность может быть приоритетом."
                )
        paragraphs.append(" ".join(vol_para))

    elif isinstance(sharpe, dict):
        sharpe_val = _as_float(sharpe.get("value"))
        sharpe_hint = sharpe.get("interpretation")
        sharpe_hint_s = str(sharpe_hint).strip() if isinstance(sharpe_hint, str) else ""
        sp: list[str] = ["Доходность на единицу риска (Шарп)."]
        if sharpe_hint_s:
            sp.append(sharpe_hint_s)
        if sharpe_val is not None:
            if sharpe_val < 0:
                sp.append(
                    "Отрицательный Шарп означает, что доходность за вычетом безрисковой ставки не окупала масштаб риска на оценочном горизонте."
                )
            elif sharpe_val < 0.5:
                sp.append(
                    "Низкий Шарп — сигнал пересмотреть соотношение доходности и волатильности (состав, доля кэша, хеджирование)."
                )
        paragraphs.append(" ".join(sp))

    mdd_block = risk.get("max_drawdown")
    if isinstance(mdd_block, dict):
        mdv = _as_float(mdd_block.get("value"))
        if mdv is not None:
            paragraphs.append(
                "Просадка (Max Drawdown). "
                "Это исторически максимальная глубина падения кривой капитала от предыдущего пика, а не один день. "
                "Она обычно существенно больше однодневного VaR, потому что отражает накопление серии неблагоприятных периодов и совместные просадки позиций. "
                "Сопоставляйте величину просадки из блока «Ключевые данные» с вашим горизонтом и лимитом по глубине просадки в политике риска."
            )

    hhi = risk.get("hhi")
    if isinstance(hhi, dict):
        pos = hhi.get("positions") if isinstance(hhi.get("positions"), dict) else None
        sec = hhi.get("sectors") if isinstance(hhi.get("sectors"), dict) else None
        hhi_parts: list[str] = ["Концентрация (HHI)."]
        if isinstance(pos, dict):
            hv = _as_float(pos.get("value"))
            hi = pos.get("interpretation")
            if hv is not None:
                hhi_parts.append(
                    f"По позициям HHI = {_format_float(hv)} ({str(hi).strip() if isinstance(hi, str) else 'оценка концентрации'}). "
                    "Чем выше HHI, тем больше доля портфеля сосредоточена в нескольких бумагах и тем сильнее вклад идосинкратического риска; "
                    "чем ниже — тем ближе к равным весам по числу имён (при прочих равных)."
                )
        if isinstance(sec, dict):
            sv = _as_float(sec.get("value"))
            si = sec.get("interpretation")
            if sv is not None:
                hhi_parts.append(
                    f"По секторам HHI = {_format_float(sv)} ({str(si).strip() if isinstance(si, str) else 'оценка концентрации'}). "
                    "Высокая отраслевая концентрация усиливает чувствительность к отраслевым шокам и «кластерным» просадкам."
                )
        if len(hhi_parts) > 1:
            paragraphs.append(" ".join(hhi_parts))

    if not paragraphs:
        return "Риск-метрики получены; для развёрнутой интерпретации нужны заполненные поля VaR, волатильности и HHI в ответе инструмента."
    return "\n\n".join(paragraphs)


def _news_trust_hint(news_data: list[dict[str, Any]]) -> str | None:
    """Краткая сводка по доверию источников в выборке новостей."""
    rows: list[dict[str, Any]] = []
    for row in news_data:
        rows.extend(_find_payload_list(row))
    if not rows:
        return None
    trust_levels: dict[str, int] = {}
    for row in rows:
        key = str(row.get("source_trust", "unknown")).upper() or "UNKNOWN"
        trust_levels[key] = trust_levels.get(key, 0) + 1
    top = sorted(trust_levels.items(), key=lambda item: (-item[1], item[0]))[:3]
    parts = [f"{name}: {count}" for name, count in top]
    return "Распределение доверия источников в выборке: " + ", ".join(parts) + "."


def _build_interpretation(
    *,
    query_type: str,
    has_market: bool,
    has_news: bool,
    has_risk: bool,
    portfolio_metrics: dict[str, Any],
    news_data: list[dict[str, Any]],
) -> str:
    """Формирует интерпретацию: опирается на фактические payload инструментов, не только на тип запроса."""
    stress = _find_payload_dict(portfolio_metrics.get("run_stress_test"))
    risk = _find_payload_dict(portfolio_metrics.get("calculate_risk_metrics"))

    if isinstance(stress, dict) and query_type == "risk_assessment":
        base = _interpret_stress_block(stress)
        if isinstance(risk, dict):
            base += " Дополнительно: " + _interpret_risk_metrics_block(risk)
        return base

    if isinstance(risk, dict) and query_type == "risk_assessment":
        return _interpret_risk_metrics_block(risk)

    if query_type == "market_monitor" and has_market:
        return (
            "Котировка отражает текущий уровень цены и ближайшую динамику сессии; для сделочных решений полезно "
            "сопоставить уровень с вашим горизонтом и допустимой просадкой."
        )
    if query_type == "news_analysis" and has_news:
        hint = _news_trust_hint(news_data)
        if hint:
            return (
                "Новостной фон разобран по заголовкам и источникам. " + hint + " Сопоставьте события с ценой и ликвидностью инструмента."
            )
        return (
            "Новостной фон разобран по заголовкам и источникам; при оценке материальности опирайтесь на дату и качество источника."
        )
    if query_type == "risk_assessment":
        return (
            "Запрос относится к устойчивости портфеля; в данных не найдено готовых метрик риска или стресс-результата для развёрнутой интерпретации."
        )
    if has_market and has_news and has_risk:
        return (
            "Запрос обработан как комплексный: сочетаются рыночные факты, новости и блок риска. "
            "Такой срез лучше отражает взаимосвязь цены, информационного фона и ограничений по риску."
        )
    if has_market or has_news or has_risk:
        return (
            "Данные частично закрывают запрос: интерпретацию стоит уточнять при появлении недостающих блоков "
            "(котировки, новости или риск-метрики)."
        )
    return "Собранные данные частично покрывают запрос; вывод ограничен доступным набором инструментов и источников."


def _build_conclusion(
    *,
    query_type: str,
    warnings: list[str],
    portfolio_metrics: dict[str, Any],
    news_data: list[dict[str, Any]],
    has_market: bool,
    has_news: bool,
    has_risk_lines: bool,
) -> list[str]:
    """Практические шаги: приоритет — специфика стресс-теста и риск-метрик, иначе — тип запроса."""
    lines: list[str] = []
    stress = _find_payload_dict(portfolio_metrics.get("run_stress_test"))
    risk = _find_payload_dict(portfolio_metrics.get("calculate_risk_metrics"))

    if isinstance(stress, dict):
        comparison = str(stress.get("current_var_comparison", "")).lower()
        scenario = str(stress.get("scenario", "")).lower()
        if "выше" in comparison:
            lines.append(
                "- Пересмотрите лимиты риска и запас ликвидности: стресс-оценка превышает типичный «плохой день» по VaR(95%)."
            )
        elif "ниже" in comparison:
            lines.append(
                "- Сопоставьте результат стресса с вашим риск-бюджетом: при модельной умеренности шока всё равно проверьте концентрацию и сценарии ликвидности."
            )
        else:
            lines.append(
                "- Зафиксируйте допущения сценария (бета, рыночный прокси, отсутствие нелинейностей) и при необходимости пересчитайте с другим горизонтом или шоком."
            )
        if scenario == "index_drop":
            lines.append(
                "- При управлении портфелем учитывайте чувствительность к рынку (бета) и долю акций: при сильной рыночной бете стресс по индексу быстрее отражается в PnL."
            )
        elif scenario == "rate_hike":
            lines.append(
                "- Для долговой части портфеля проверьте дюрацию и чувствительность к ставке; для акций секторов «ставочной» чувствительности — отдельный взгляд на драйверы."
            )
        elif scenario == "sector_decline":
            lines.append(
                "- При сильной отраслевой концентрации рассмотрите диверсификацию или хедж по сектору, если это соответствует вашей стратегии."
            )
    elif isinstance(risk, dict) and query_type == "risk_assessment":
        lines.append(
            "- Сопоставьте VaR/CVaR с лимитами фонда/стратегии; при высокой волатильности и HHI сузьте концентрацию или уменьшите размер позиций."
        )
        lines.append(
            "- Пересчитайте метрики после значимых сделок или изменения состава — однодневные оценки чувствительны к выбросам в истории."
        )
    elif query_type == "market_monitor" and has_market:
        lines.append("- Зафиксируйте уровень входа/стопа относительно текущей котировки и времени последнего обновления.")
    elif query_type == "news_analysis" and has_news:
        lines.append(
            "- Отфильтруйте заголовки по дате и доверию источника; подтвердите факты первичными документами (отчёт, пресс-релиз, регулятор)."
        )
    elif query_type == "risk_assessment" and has_risk_lines:
        lines.append("- Сверьте риск-метрики с внутренними лимитами и стресс-сценариями, зафиксированными в вашей политике.")
    elif query_type == "complex" and (has_market or has_news or has_risk_lines):
        lines.append(
            "- Сведите воедино цену, новости и риск: меняйте веса только если все три сигнала согласованы с вашим горизонтом."
        )
    else:
        lines.append(
            "- Уточните запрос (тикер, портфель, горизонт) и при необходимости повторите после восстановления источников данных."
        )

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
    # Важно: не экранируем весь payload до парсинга, иначе JSON в MCP text-контенте
    # становится невалидным и новости перестают извлекаться.
    news_data = list(state.get("news_data", []))
    portfolio_metrics = dict(state.get("portfolio_metrics", {}))
    warnings = list(state.get("warnings", []))

    market_lines = _extract_market_lines(market_data)
    news_lines = _extract_news_lines(news_data)
    risk_lines = _extract_risk_lines(portfolio_metrics)

    has_market = bool(market_lines)
    has_news = bool(news_lines)
    has_risk = bool(risk_lines)
    interpretation = _build_interpretation(
        query_type=query_type,
        has_market=has_market,
        has_news=has_news,
        has_risk=has_risk,
        portfolio_metrics=portfolio_metrics,
        news_data=news_data,
    )
    conclusion_lines = _build_conclusion(
        query_type=query_type,
        warnings=warnings,
        portfolio_metrics=portfolio_metrics,
        news_data=news_data,
        has_market=has_market,
        has_news=has_news,
        has_risk_lines=has_risk,
    )

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

