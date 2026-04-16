"""Каталог сценариев и моделей для сравнительного бенчмарка."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BenchmarkScenario:
    """Базовый сценарий для сравнения систем."""

    name: str
    query: str
    expected_servers: set[str]


DEFAULT_BENCHMARK_SCENARIOS: tuple[BenchmarkScenario, ...] = (
    BenchmarkScenario(
        name="market_only",
        query="Покажи котировку SBER",
        expected_servers={"market"},
    ),
    BenchmarkScenario(
        name="news_only",
        query="Последние новости по Газпрому",
        expected_servers={"news"},
    ),
    BenchmarkScenario(
        name="risk_assessment",
        query="Оцени риск портфеля demo_portfolio",
        expected_servers={"analytics"},
    ),
    BenchmarkScenario(
        name="stress_test",
        query="Проведи стресс-тест портфеля demo_portfolio при падении IMOEX на 20%",
        expected_servers={"analytics"},
    ),
    BenchmarkScenario(
        name="complex",
        query="Оцени риск портфеля demo_portfolio с учетом новостей нефтегаза и котировки GAZP",
        expected_servers={"market", "news", "analytics"},
    ),
    BenchmarkScenario(
        name="investment_decision_intent",
        query="Стоит ли сейчас покупать Сбербанк с учетом новостей и риска?",
        expected_servers={"market", "news"},
    ),
)


DEFAULT_BENCHMARK_MODELS: tuple[str, ...] = (
    "aliceai-llm/latest",
    "deepseek-v32/latest",
    "gpt-oss-120b/latest",
    "gpt-oss-20b/latest",
    "gemma-3-27b-it/latest",
    "qwen3-235b-a22b-fp8/latest",
    "qwen3.5-35b-a3b-fp8/latest",
    "yandexgpt-5-lite/latest",
    "yandexgpt-5-pro/latest",
    "yandexgpt-5.1/latest",
)
