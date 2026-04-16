"""Тесты системных промптов оркестратора."""

from __future__ import annotations

from orchestrator.prompts import PLANNER_SYSTEM_PROMPT


def test_planner_prompt_describes_target_server_semantics() -> None:
    """Промпт явно объясняет роли target_server и их назначение."""
    assert "Что такое target_server" in PLANNER_SYSTEM_PROMPT
    assert "market_executor" in PLANNER_SYSTEM_PROMPT
    assert "news_executor" in PLANNER_SYSTEM_PROMPT
    assert "analytics_executor" in PLANNER_SYSTEM_PROMPT
    assert "summarizer" in PLANNER_SYSTEM_PROMPT


def test_planner_prompt_contains_priority_policy_and_anti_failure_rules() -> None:
    """Промпт содержит приоритетные правила и анти-ошибочные ограничения."""
    assert "Decision policy (строгий приоритет)" in PLANNER_SYSTEM_PROMPT
    assert "query_type = risk_assessment" in PLANNER_SYSTEM_PROMPT
    assert "scenario=sector_decline" in PLANNER_SYSTEM_PROMPT
    assert "Не используй placeholder-значения" in PLANNER_SYSTEM_PROMPT
    assert "demo_portfolio" in PLANNER_SYSTEM_PROMPT
    assert "мagnitude передавай процентные пункты" in PLANNER_SYSTEM_PROMPT or "процентные пункты" in PLANNER_SYSTEM_PROMPT
    assert "ТОЛЬКО валидный JSON" in PLANNER_SYSTEM_PROMPT
