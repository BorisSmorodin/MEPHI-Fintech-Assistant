"""Сценарное стресс-тестирование портфеля."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from servers.analytics_server.risk_calculator import build_portfolio_series

FINANCIAL_SENSITIVE_SECTORS = {"финансы", "строительство"}

# Подсказки эмитента/тикера в target_sector (LLM часто передаёт «Yandex», тогда как в БД сектор «IT»).
_ISSUER_HINT_TO_TICKERS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"яндекс", "yandex", "yndx", "ydex"}), frozenset({"YNDX", "YDEX"})),
    (frozenset({"газпром", "gazprom", "gazp"}), frozenset({"GAZP"})),
    (frozenset({"сбер", "sber", "сбербанк", "sberbank"}), frozenset({"SBER"})),
    (frozenset({"лукойл", "lukoil", "lkoh"}), frozenset({"LKOH"})),
    (frozenset({"роснефт", "rosneft", "rosn"}), frozenset({"ROSN"})),
    (frozenset({"норникель", "nornickel", "gmkn"}), frozenset({"GMKN"})),
)


def _to_position_map(
    positions: list[dict],
    current_prices: dict[str, float],
) -> tuple[list[dict[str, Any]], float]:
    """Преобразует позиции в список с рыночной стоимостью."""
    converted: list[dict[str, Any]] = []
    total_value = 0.0
    for row in positions:
        ticker = str(row["ticker"])
        quantity = float(row["quantity"])
        current_price = float(current_prices.get(ticker, 0.0))
        market_value = quantity * current_price
        total_value += market_value
        converted.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "sector": str(row["sector"]),
                "instrument_type": str(row["instrument_type"]),
                "current_price": current_price,
                "market_value": market_value,
            }
        )
    return converted, total_value


def _estimate_betas(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
) -> dict[str, float]:
    """Оценивает beta по OLS относительно рыночного прокси."""
    series = build_portfolio_series(positions, current_prices, price_rows)
    ticker_returns: dict[str, np.ndarray] = {}
    for idx, ticker in enumerate(series.tickers):
        ticker_returns[ticker] = series.returns_matrix[:, idx]

    equity_returns = [
        ticker_returns[position["ticker"]]
        for position in positions
        if str(position["instrument_type"]) == "акция" and position["ticker"] in ticker_returns
    ]
    if equity_returns:
        market_proxy = np.mean(np.vstack(equity_returns), axis=0)
    else:
        market_proxy = series.portfolio_returns

    market_var = float(np.var(market_proxy, ddof=1))
    betas: dict[str, float] = {}
    for ticker, returns in ticker_returns.items():
        if market_var <= 1e-12:
            beta = 1.0
        else:
            covariance = float(np.cov(returns, market_proxy, ddof=1)[0, 1])
            beta = covariance / market_var
        betas[ticker] = max(0.0, float(beta))
    return betas


def _estimate_current_var_pct(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
    confidence: float = 0.95,
) -> float:
    """Оценивает текущий исторический VaR в процентах для сравнения со стрессом."""
    series = build_portfolio_series(positions, current_prices, price_rows)
    quantile = float(np.quantile(series.portfolio_returns, 1.0 - confidence))
    return max(0.0, -quantile)


def _run_index_drop(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
    magnitude: float,
) -> tuple[list[dict], float]:
    """Сценарий падения индекса с beta-переоценкой."""
    betas = _estimate_betas(positions, current_prices, price_rows)
    converted, _ = _to_position_map(positions, current_prices)

    affected_positions: list[dict] = []
    total_loss = 0.0
    shock = abs(magnitude) / 100.0
    for position in converted:
        ticker = position["ticker"]
        beta = betas.get(ticker, 0.2 if position["instrument_type"] == "облигация" else 1.0)
        loss_pct = min(0.95, max(0.0, beta * shock))
        loss_rub = position["market_value"] * loss_pct
        total_loss += loss_rub
        affected_positions.append(
            {
                "ticker": ticker,
                "loss_rub": round(loss_rub, 2),
                "loss_pct": round(loss_pct, 6),
                "beta": round(beta, 4),
            }
        )
    return affected_positions, total_loss


def _run_rate_hike(
    positions: list[dict],
    current_prices: dict[str, float],
    magnitude: float,
    bond_details: dict[str, dict] | None = None,
) -> tuple[list[dict], float]:
    """Сценарий роста ставки с переоценкой облигаций и чувствительных акций."""
    converted, _ = _to_position_map(positions, current_prices)
    delta_rate = abs(magnitude) / 100.0
    details = bond_details or {}

    affected_positions: list[dict] = []
    total_loss = 0.0
    for position in converted:
        loss_pct = 0.0
        if position["instrument_type"] == "облигация":
            duration = float(details.get(position["ticker"], {}).get("duration", 4.0))
            loss_pct = min(0.95, max(0.0, duration * delta_rate))
        elif position["sector"] in FINANCIAL_SENSITIVE_SECTORS:
            loss_pct = min(0.95, 0.5 * abs(magnitude) / 100.0)

        if loss_pct <= 0.0:
            continue

        loss_rub = position["market_value"] * loss_pct
        total_loss += loss_rub
        affected_positions.append(
            {
                "ticker": position["ticker"],
                "loss_rub": round(loss_rub, 2),
                "loss_pct": round(loss_pct, 6),
            }
        )
    return affected_positions, total_loss


def _tickers_from_issuer_hint(hint: str) -> frozenset[str] | None:
    """Возвращает тикеры по текстовой подсказке (название компании / тикер в нижнем регистре)."""
    h = hint.strip().casefold()
    if not h:
        return None
    for synonyms, tickers in _ISSUER_HINT_TO_TICKERS:
        if h in synonyms or any(s in h for s in synonyms):
            return tickers
    return None


def _select_sector_decline_positions(
    converted: list[dict],
    target_sector: str | None,
) -> tuple[list[dict], str]:
    """Подбирает позиции: сектор (без учёта регистра), тикер, подсказка эмитента или крупнейший сектор."""
    sector_value_map: dict[str, float] = defaultdict(float)
    for position in converted:
        sector_value_map[position["sector"]] += position["market_value"]

    if not target_sector or not str(target_sector).strip():
        selected_label = max(sector_value_map.items(), key=lambda item: item[1])[0]
        matched = [p for p in converted if p["sector"] == selected_label]
        return matched, selected_label

    hint_raw = str(target_sector).strip()
    hint_cf = hint_raw.casefold()
    tickers_in_portfolio = {p["ticker"] for p in converted}

    # Явный тикер в target_sector (например YNDX).
    hint_upper = hint_raw.upper()
    if hint_upper in tickers_in_portfolio:
        matched = [p for p in converted if p["ticker"] == hint_upper]
        return matched, matched[0]["sector"]

    # Совпадение названия сектора без учёта регистра.
    matched = [p for p in converted if p["sector"].casefold() == hint_cf]
    if matched:
        return matched, matched[0]["sector"]

    # Подстрока в названии сектора.
    matched = [p for p in converted if hint_cf in p["sector"].casefold()]
    if matched:
        return matched, matched[0]["sector"]

    # Известные эмитенты (Yandex при sector=IT в данных).
    issuer_tickers = _tickers_from_issuer_hint(hint_raw)
    if issuer_tickers:
        matched = [p for p in converted if p["ticker"] in issuer_tickers]
        if matched:
            return matched, matched[0]["sector"]

    return [], hint_raw


def _run_sector_decline(
    positions: list[dict],
    current_prices: dict[str, float],
    magnitude: float,
    target_sector: str | None,
) -> tuple[list[dict], float, str]:
    """Сценарий падения сектора (или выбранных позиций по эмитенту) на заданный процент."""
    converted, _ = _to_position_map(positions, current_prices)
    if not converted:
        return [], 0.0, target_sector or ""

    matched_positions, selected_label = _select_sector_decline_positions(converted, target_sector)

    shock = abs(magnitude) / 100.0
    affected_positions: list[dict] = []
    total_loss = 0.0
    for position in matched_positions:
        loss_rub = position["market_value"] * shock
        total_loss += loss_rub
        affected_positions.append(
            {
                "ticker": position["ticker"],
                "sector": position["sector"],
                "loss_rub": round(loss_rub, 2),
                "loss_pct": round(shock, 6),
            }
        )
    return affected_positions, total_loss, selected_label


def run_stress_test(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
    scenario: str,
    magnitude: float,
    *,
    target_sector: str | None = None,
    bond_details: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Выполняет сценарный стресс-тест портфеля."""
    if magnitude <= 0:
        raise ValueError("Параметр magnitude должен быть положительным.")

    normalized_scenario = scenario.strip().lower()
    converted, total_value = _to_position_map(positions, current_prices)
    if total_value <= 0:
        raise ValueError("Невозможно выполнить стресс-тест: портфель пуст или неоценен.")

    selected_sector = target_sector
    if normalized_scenario == "index_drop":
        affected_positions, total_loss = _run_index_drop(positions, current_prices, price_rows, magnitude)
    elif normalized_scenario == "rate_hike":
        affected_positions, total_loss = _run_rate_hike(
            positions, current_prices, magnitude, bond_details=bond_details
        )
    elif normalized_scenario == "sector_decline":
        affected_positions, total_loss, selected_sector = _run_sector_decline(
            positions, current_prices, magnitude, target_sector
        )
    else:
        raise ValueError(f"Неподдерживаемый сценарий стресс-теста: {scenario}")

    total_loss_pct = total_loss / total_value if total_value else 0.0
    current_var_pct = _estimate_current_var_pct(positions, current_prices, price_rows, confidence=0.95)
    if total_loss_pct > current_var_pct:
        comparison = "Стресс-потеря выше текущего однодневного VaR(95%)."
    elif abs(total_loss_pct - current_var_pct) < 1e-9:
        comparison = "Стресс-потеря сопоставима с текущим однодневным VaR(95%)."
    else:
        comparison = "Стресс-потеря ниже текущего однодневного VaR(95%)."

    response: dict[str, Any] = {
        "scenario": normalized_scenario,
        "magnitude": magnitude,
        "total_loss_rub": round(total_loss, 2),
        "total_loss_pct": round(total_loss_pct, 6),
        "affected_positions": affected_positions,
        "current_var_comparison": comparison,
        "portfolio_value_rub": round(total_value, 2),
        "affected_positions_count": len(affected_positions),
    }
    if normalized_scenario == "sector_decline":
        response["target_sector"] = selected_sector
    return response

