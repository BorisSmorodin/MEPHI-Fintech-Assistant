"""Проверка: возвращает ли Yandex/OpenAI Responses API поле usage для вызова как в planner_node.

Запуск из корня репозитория:
    uv run python scripts/check_planner_usage.py
"""

from __future__ import annotations

import json
import sys

from openai import OpenAI

from config.settings import get_settings
from orchestrator.nodes.planner_node import _extract_planner_token_usage
from orchestrator.prompts import PLANNER_SYSTEM_PROMPT


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    settings = get_settings()
    if not settings.yandex_cloud_api_key or not settings.yandex_cloud_folder:
        print("Нет YANDEX_CLOUD_API_KEY или YANDEX_CLOUD_FOLDER в настройках — проверка невозможна.")
        return 1

    client = OpenAI(
        api_key=settings.yandex_cloud_api_key,
        base_url=settings.llm_base_url,
        project=settings.yandex_cloud_folder,
    )
    model_name = f"gpt://{settings.yandex_cloud_folder}/{settings.yandex_cloud_model}"
    prompt_input = json.dumps(
        {
            "user_query": "Покажи котировку SBER",
            "query_type": "market_monitor",
            "extracted_tickers": ["SBER"],
        },
        ensure_ascii=False,
    )

    print(f"Модель: {settings.yandex_cloud_model}")
    print(f"Base URL: {settings.llm_base_url}")
    print("Вызов client.responses.create (как в planner_node)...")

    response = client.responses.create(
        model=model_name,
        temperature=0.1,
        instructions=PLANNER_SYSTEM_PROMPT,
        input=prompt_input,
        max_output_tokens=256,
    )

    usage_obj = getattr(response, "usage", None)
    print("\n--- response.usage (сырой объект) ---")
    if usage_obj is None:
        print("usage = None (провайдер/SDK не вернули usage)")
    else:
        if hasattr(usage_obj, "model_dump"):
            print(json.dumps(usage_obj.model_dump(), ensure_ascii=False, indent=2))
        else:
            print(repr(usage_obj))

    raw_text = response.output_text or ""
    parsed = _extract_planner_token_usage(
        response,
        prompt_input=prompt_input,
        output_text=raw_text,
    )
    print("\n--- как интерпретирует _extract_planner_token_usage ---")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))

    if parsed.get("planner_tokens_estimated"):
        print(
            "\nИтог: usage отсутствовал или все счётчики были 0 — "
            "сработала оценка по длине prompt/output (planner_tokens_estimated=True)."
        )
    else:
        print("\nИтог: в ответе есть ненулевые токены из usage (оценка по тексту не нужна).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
