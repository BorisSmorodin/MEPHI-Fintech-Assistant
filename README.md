# Investment Assistant

Прототип инвестиционного ассистента для мониторинга российского рынка, анализа новостей
и оценки рисков портфеля на базе `LangGraph + MCP`.

## Технологический стек

- Python 3.11+
- `uv` для управления зависимостями
- `FastMCP` для MCP-серверов (`market`, `news`, `analytics`)
- `LangGraph` для оркестрации
- `Streamlit` и `Typer` для UI

## Провайдер LLM

- В проекте используется Yandex Cloud через OpenAI-compatible API.
- Базовый URL: `https://ai.api.cloud.yandex.net/v1`
- Формат модели при прямом вызове API: `gpt://<folder>/<model>`

## Основной оркестратор и ReAct-агент для сравнения

- **Основная система** — граф в `orchestrator/`: классификация запроса, планировщик (LLM + fallback и sanitize/repair), исполнители MCP и детерминированный summarizer. Это целевой путь продукта.
- **Альтернатива для тестов и сравнения** — пакет `react_agent/`: один **ReAct**-цикл на **LangGraph** (`langgraph.prebuilt.create_react_agent`), подключённый к **тем же MCP-серверам** через общий клиент [`orchestrator/mcp_client.py`](orchestrator/mcp_client.py) (тот же набор инструментов `market` / `news` / `analytics`, без дублирования серверной логики).
- Модель для ReAct — **chat completions** (`langchain_openai.ChatOpenAI`, конфигурация из `config/settings.py`), в отличие от планировщика оркестратора, который использует **Responses API** для JSON-плана.
- Результат `async run_react_query(...)` содержит `messages`, **`tool_trace`** (имя инструмента, аргументы, превью ответа tool) и `final_answer` — по ним удобно сопоставлять **выбор инструментов и аргументы** с планом и фактическими вызовами оркестратора.
- Пример запуска из кода (после поднятия MCP и настройки `.env`):

```python
import asyncio
from react_agent import run_react_query

async def main() -> None:
    out = await run_react_query("Покажи котировку SBER")
    print(out["tool_trace"])
    print(out["final_answer"])

asyncio.run(main())
```

- E2e из консоли (после запуска трёх MCP-серверов и настройки `.env`): `python -m ui.cli react` (REPL) или одноразово: `python -m ui.cli react -q "Покажи котировку SBER"`.

## Полный запуск проекта

### 1) Подготовка окружения

1. Скопировать шаблон окружения:
  - `.env.example` -> `.env`
2. Заполнить обязательные параметры (см. таблицу ниже).
3. Установить зависимости:
  - `uv sync --group dev`

### 2) Запуск MCP-серверов

Откройте отдельный терминал для каждого сервера:

- Market server:
  - `python -m servers.market_server.server`
- News server:
  - `python -m servers.news_server.server`
- Analytics server:
  - `python -m servers.analytics_server.server`

### 3) Запуск пользовательских интерфейсов

- CLI:
  - `python -m ui.cli`
  - или явно: `python -m ui.cli chat`
  - с управлением глубиной ответа: `python -m ui.cli chat --answer-depth auto|compact|standard`
  - ReAct-агент (e2e, сравнение с оркестратором): `python -m ui.cli react` или `python -m ui.cli react -q "..."`
  - Массовый benchmark моделей (оркестратор/ReAct): `python -m ui.cli benchmark-models --system both`
- Streamlit:
  - `streamlit run ui/streamlit_app.py`

### 4) Запуск тестов

- Полный прогон:
  - `pytest -q`
- Целевые наборы:
  - `pytest -q tests/test_orchestrator.py tests/test_e2e.py`
  - `pytest -q servers/market_server/tests/test_market.py`
  - `pytest -q servers/news_server/tests/test_news.py`
  - `pytest -q servers/analytics_server/tests/test_analytics.py`

### 5) Отчет по метрикам качества

- Сводный отчет из JSONL:
  - `python tests/quality_metrics_report.py --metrics-path data/fixtures/quality_metrics.jsonl`

### 6) Диагностика запуска CLI/MCP

- REPL CLI можно запускать как `python -m ui.cli`, так и `python -m ui.cli chat`.
- Для ручного переключения глубины суммаризации используйте `--answer-depth auto|compact|standard`.
- Ошибка `Failed to parse JSONRPC message from server`:
  - убедитесь, что MCP-сервисы не пишут служебные логи в `stdout`;
  - перезапустите CLI после обновления окружения/зависимостей.
- Ошибка ClickHouse `WinError 10061`:
  - либо запустите локальный ClickHouse;
  - либо включите mock-режим (`USE_MOCK_CLICKHOUSE=true`);
  - проверьте параметры `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`.

## Переменные окружения


| Переменная                    | Обязательность                   | Назначение                                | Пример                                |
| ----------------------------- | -------------------------------- | ----------------------------------------- | ------------------------------------- |
| `YANDEX_CLOUD_API_KEY`        | Обязательная                     | API-ключ Yandex Cloud LLM                 | `AQVN...`                             |
| `YANDEX_CLOUD_FOLDER`         | Обязательная                     | ID каталога Yandex Cloud                  | `b1gur7u1r8761kpsbj6g`                |
| `YANDEX_CLOUD_MODEL`          | Обязательная                     | Базовая LLM-модель                        | `gpt-oss-120b/latest`                 |
| `CLICKHOUSE_PASSWORD`         | Обязательная для real ClickHouse | Пароль read-only пользователя CH          | `readonly_password`                   |
| `USE_MOCK_CLICKHOUSE`         | Опциональная                     | Использование фикстур вместо реального CH | `true`                                |
| `CLICKHOUSE_HOST`             | Опциональная                     | Хост ClickHouse                           | `localhost`                           |
| `CLICKHOUSE_PORT`             | Опциональная                     | Порт ClickHouse                           | `8123`                                |
| `CLICKHOUSE_DATABASE`         | Опциональная                     | База ClickHouse                           | `investment`                          |
| `CLICKHOUSE_USER`             | Опциональная                     | Read-only пользователь CH                 | `readonly_user`                       |
| `LLM_BASE_URL`                | Опциональная                     | OpenAI-compatible endpoint                | `https://ai.api.cloud.yandex.net/v1`  |
| `LLM_MODEL`                   | Опциональная                     | Альтернативное имя модели                 | `gpt-oss-120b/latest`                 |
| `LLM_TEMPERATURE`             | Опциональная                     | Температура генерации                     | `0.1`                                 |
| `MAX_RECURSION`               | Опциональная                     | Лимит рекурсии LangGraph                  | `10`                                  |
| `MAX_ERROR_COUNT`             | Опциональная                     | Лимит ошибок перед деградацией            | `3`                                   |
| `MARKET_SERVER_TIMEOUT`       | Опциональная                     | Таймаут market API вызовов                | `30`                                  |
| `NEWS_SERVER_CACHE_TTL`       | Опциональная                     | TTL кэша RSS                              | `300`                                 |
| `SUMMARY_PREFER_COMPACT_FOR_COMPLEX` | Опциональная              | Предпочитать compact-ответы для complex   | `true`                                |
| `QUALITY_METRICS_PATH`        | Опциональная                     | Путь к JSONL-метрикам                     | `data/fixtures/quality_metrics.jsonl` |
| `QUALITY_METRICS_ENABLE_FILE` | Опциональная                     | Включение записи метрик в файл            | `false`                               |


## Известные ограничения прототипа

- MOEX ISS API публичный, данные могут поступать с задержкой.
- Тональность новостей определяется эвристически, без отдельной ML/LLM-классификации.
- Стресс-тесты и risk-метрики реализованы в упрощенной параметрической постановке.
- Planner строит план через LLM, но использует fallback, sanitize и contract-repair для устойчивости к плохим LLM-ответам.
- Summarizer формирует итоговый ответ детерминированно из `state`; глубина интерпретации может переключаться через `answer_depth`.
- Система предназначена для учебно-исследовательских задач и не является инвестиционной рекомендацией.

## Полезные документы

- Техническое задание: `docs/TZ_InvestmentAssistant.md`
- План реализации: `docs/Implementation_Plan_InvestmentAssistant.md`
- Отчет по этапам: `docs/Work_Report_By_Stages.md`
- Потоки данных и архитектура: `docs/Architecture_and_Data_Flows.md`
- Контракты MCP: `docs/MCP_Tool_Contracts.md`
- Переходы состояния LangGraph: `docs/LangGraph_State_Flow.md`
- Сценарии предзащиты: `docs/Defense_Demo_Scenarios.md`
- Runbook чистой машины: `docs/Clean_Machine_Runbook.md`

