"""Единая конфигурация проекта Investment Assistant."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения, загружаемая из .env и переменных окружения."""

    # LLM
    yandex_cloud_api_key: str = Field(default="", alias="YANDEX_CLOUD_API_KEY")
    yandex_cloud_folder: str = Field(default="", alias="YANDEX_CLOUD_FOLDER")
    yandex_cloud_model: str = Field(default="aliceai-llm/latest", alias="YANDEX_CLOUD_MODEL")
    llm_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    llm_model: str = "aliceai-llm/latest"
    llm_temperature: float = 0.1

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "investment"
    clickhouse_user: str = "readonly_user"
    clickhouse_password: str = Field(default="", alias="CLICKHOUSE_PASSWORD")
    clickhouse_query_timeout_sec: int = 30

    # Серверы и кэш
    market_server_timeout: int = 30
    market_quote_cache_ttl: int = 60
    market_history_cache_ttl: int = 300
    news_server_cache_ttl: int = 300
    news_http_timeout_sec: int = 10

    # Оркестратор
    max_recursion: int = 20
    max_error_count: int = 3
    quality_metrics_path: str = "data/fixtures/quality_metrics.jsonl"
    quality_metrics_enable_file: bool = False

    # Dev
    use_mock_clickhouse: bool = False
    allow_mock_fallback_on_clickhouse_error: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек приложения."""
    return Settings()

