"""Расчет риск-метрик портфеля."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import norm

TRADING_DAYS_PER_YEAR = 252


@dataclass(slots=True)
class PortfolioSeries:
    """Контейнер подготовленных данных портфеля для риск-расчетов."""

    tickers: list[str]
    sectors: list[str]
    weights: np.ndarray
    portfolio_value: float
    returns_matrix: np.ndarray
    portfolio_returns: np.ndarray


def _build_price_matrix(price_rows: list[dict], tickers: list[str]) -> np.ndarray:
    """Строит выровненную матрицу цен по тикерам.

    Для упрощения выравнивания используется хвост минимальной длины из
    доступных ценовых рядов.
    """
    grouped_prices: dict[str, list[float]] = defaultdict(list)
    grouped_dates: dict[str, list[str]] = defaultdict(list)
    for row in price_rows:
        ticker = str(row["ticker"])
        if ticker in tickers:
            grouped_dates[ticker].append(str(row["date"]))
            grouped_prices[ticker].append(float(row["close"]))

    ordered_tickers = [ticker for ticker in tickers if ticker in grouped_prices]
    if not ordered_tickers:
        raise ValueError("Недостаточно данных price_history для расчета риска.")

    for ticker in ordered_tickers:
        paired = sorted(zip(grouped_dates[ticker], grouped_prices[ticker], strict=False), key=lambda item: item[0])
        grouped_prices[ticker] = [price for _, price in paired]

    min_len = min(len(grouped_prices[ticker]) for ticker in ordered_tickers)
    if min_len < 3:
        raise ValueError("Недостаточно наблюдений для расчета доходностей.")

    aligned_prices = np.array([grouped_prices[ticker][-min_len:] for ticker in ordered_tickers], dtype=float)
    return aligned_prices.T


def _compute_returns_matrix(price_matrix: np.ndarray) -> np.ndarray:
    """Преобразует матрицу цен в матрицу простых дневных доходностей."""
    return np.diff(price_matrix, axis=0) / price_matrix[:-1, :]


def _compute_weights(
    positions: list[dict],
    current_prices: dict[str, float],
) -> tuple[list[str], list[str], np.ndarray, float]:
    """Вычисляет веса позиций в портфеле."""
    tickers: list[str] = []
    sectors: list[str] = []
    market_values: list[float] = []
    for position in positions:
        ticker = str(position["ticker"])
        quantity = float(position["quantity"])
        current_price = float(current_prices.get(ticker, 0.0))
        market_value = quantity * current_price
        if market_value <= 0.0:
            continue
        tickers.append(ticker)
        sectors.append(str(position["sector"]))
        market_values.append(market_value)

    if not market_values:
        raise ValueError("Невозможно рассчитать веса: отсутствуют рыночные стоимости позиций.")

    values = np.array(market_values, dtype=float)
    portfolio_value = float(np.sum(values))
    weights = values / portfolio_value
    return tickers, sectors, weights, portfolio_value


def _interpret_volatility(volatility_annual: float) -> str:
    """Возвращает текстовую интерпретацию волатильности."""
    if volatility_annual < 0.15:
        return "Низкая волатильность портфеля."
    if volatility_annual < 0.30:
        return "Умеренная волатильность портфеля."
    return "Высокая волатильность портфеля."


def _interpret_sharpe(sharpe_ratio: float) -> str:
    """Возвращает интерпретацию коэффициента Шарпа."""
    if sharpe_ratio < 0:
        return "Доходность на единицу риска отрицательная."
    if sharpe_ratio < 1:
        return "Умеренное соотношение доходности и риска."
    if sharpe_ratio < 2:
        return "Хорошее соотношение доходности и риска."
    return "Очень высокое соотношение доходности и риска."


def _interpret_hhi(hhi_value: float) -> str:
    """Интерпретирует HHI по базовым порогам концентрации."""
    if hhi_value < 0.10:
        return "Низкая концентрация."
    if hhi_value < 0.18:
        return "Умеренная концентрация."
    return "Высокая концентрация."


def _compute_max_drawdown(returns: np.ndarray) -> float:
    """Вычисляет максимальную просадку кумулятивной доходности."""
    cumulative = np.cumprod(1.0 + returns)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = cumulative / running_max - 1.0
    return float(abs(np.min(drawdowns)))


def build_portfolio_series(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
) -> PortfolioSeries:
    """Готовит унифицированные ряды для риск-расчетов и стресс-тестов."""
    tickers, sectors, weights, portfolio_value = _compute_weights(positions, current_prices)
    price_matrix = _build_price_matrix(price_rows, tickers)
    returns_matrix = _compute_returns_matrix(price_matrix)
    portfolio_returns = returns_matrix @ weights
    return PortfolioSeries(
        tickers=tickers,
        sectors=sectors,
        weights=weights,
        portfolio_value=portfolio_value,
        returns_matrix=returns_matrix,
        portfolio_returns=portfolio_returns,
    )


def calculate_risk_metrics(
    positions: list[dict],
    current_prices: dict[str, float],
    price_rows: list[dict],
    confidence: float = 0.95,
    risk_free_rate_annual: float = 0.21,
) -> dict[str, Any]:
    """Рассчитывает основные риск-метрики портфеля по ТЗ."""
    if not 0.5 < confidence < 0.999:
        raise ValueError("Параметр confidence должен быть в диапазоне (0.5, 0.999).")

    series = build_portfolio_series(positions, current_prices, price_rows)
    returns = series.portfolio_returns
    mean_daily = float(np.mean(returns))
    volatility_daily = float(np.std(returns, ddof=1))
    volatility_annual = float(volatility_daily * np.sqrt(TRADING_DAYS_PER_YEAR))
    annual_return = float(mean_daily * TRADING_DAYS_PER_YEAR)

    alpha = 1.0 - confidence
    quantile_value = float(np.quantile(returns, alpha))
    historical_var_pct = max(0.0, -quantile_value)
    historical_var_rub = historical_var_pct * series.portfolio_value

    z_score = float(norm.ppf(confidence))
    parametric_var_pct = max(0.0, -(mean_daily - z_score * volatility_daily))
    parametric_var_rub = parametric_var_pct * series.portfolio_value

    tail = returns[returns <= quantile_value]
    cvar_pct = max(0.0, float(-np.mean(tail))) if tail.size else historical_var_pct
    cvar_rub = cvar_pct * series.portfolio_value

    max_drawdown = _compute_max_drawdown(returns)
    sharpe_ratio = 0.0
    if volatility_annual > 1e-12:
        sharpe_ratio = float((annual_return - risk_free_rate_annual) / volatility_annual)

    hhi_positions = float(np.sum(np.square(series.weights)))
    sector_weights: dict[str, float] = defaultdict(float)
    for sector, weight in zip(series.sectors, series.weights, strict=False):
        sector_weights[sector] += float(weight)
    hhi_sectors = float(np.sum(np.square(np.array(list(sector_weights.values()), dtype=float))))

    return {
        "confidence": confidence,
        "portfolio_value_rub": round(series.portfolio_value, 2),
        "mean_daily_return": round(mean_daily, 6),
        "annual_return": round(annual_return, 6),
        "var_historical": {
            "value_rub": round(historical_var_rub, 2),
            "value_pct": round(historical_var_pct, 6),
            "interpretation": "Потеря за день при историческом подходе на заданном доверии.",
        },
        "var_parametric": {
            "value_rub": round(parametric_var_rub, 2),
            "value_pct": round(parametric_var_pct, 6),
            "interpretation": "Потеря за день при нормальном предположении распределения доходностей.",
        },
        "cvar": {
            "value_rub": round(cvar_rub, 2),
            "value_pct": round(cvar_pct, 6),
            "interpretation": "Средняя потеря в хвосте распределения хуже VaR.",
        },
        "volatility": {
            "value_annual": round(volatility_annual, 6),
            "interpretation": _interpret_volatility(volatility_annual),
        },
        "sharpe": {
            "value": round(sharpe_ratio, 6),
            "risk_free_rate_annual": risk_free_rate_annual,
            "interpretation": _interpret_sharpe(sharpe_ratio),
        },
        "max_drawdown": {
            "value": round(max_drawdown, 6),
            "interpretation": "Максимальная наблюдаемая просадка кумулятивной доходности.",
        },
        "hhi": {
            "positions": {
                "value": round(hhi_positions, 6),
                "interpretation": _interpret_hhi(hhi_positions),
            },
            "sectors": {
                "value": round(hhi_sectors, 6),
                "interpretation": _interpret_hhi(hhi_sectors),
            },
        },
        "observations": int(returns.size),
    }

