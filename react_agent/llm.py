"""Фабрика chat-модели для ReAct (OpenAI-compatible API, в т.ч. Yandex Cloud)."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from config.settings import Settings, get_settings


def resolve_chat_model_name(settings: Settings) -> str:
    """Имя модели в формате, совместимом с Yandex Cloud OpenAI-compatible chat API."""
    if settings.yandex_cloud_folder:
        base = (settings.llm_model or settings.yandex_cloud_model).strip()
        return f"gpt://{settings.yandex_cloud_folder}/{base}"
    return (settings.llm_model or settings.yandex_cloud_model).strip()


def build_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Собирает ChatOpenAI из единого конфига проекта."""
    cfg = settings or get_settings()
    if not cfg.yandex_cloud_api_key.strip():
        msg = "Для ReAct-агента нужен YANDEX_CLOUD_API_KEY в окружении или .env."
        raise RuntimeError(msg)
    if not (cfg.llm_model or cfg.yandex_cloud_model).strip():
        msg = "Укажите LLM_MODEL или YANDEX_CLOUD_MODEL."
        raise RuntimeError(msg)
    model_name = resolve_chat_model_name(cfg)
    return ChatOpenAI(
        model=model_name,
        api_key=cfg.yandex_cloud_api_key,
        base_url=cfg.llm_base_url,
        temperature=cfg.llm_temperature,
    )
