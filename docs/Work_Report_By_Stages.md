# Отчет о выполненных работах по этапам

## Контекст

- Документ фиксирует фактические результаты реализации по этапам из
  `docs/Implementation_Plan_InvestmentAssistant.md`.
- Состояние отчета: актуально на момент выполнения пользовательских замечаний
  по провайдеру LLM (переход на Yandex Cloud).

## Этап 0. Инициация и подготовка среды

### 0.1 Базовый аудит репозитория

Выполнено:

- Проанализировано исходное состояние репозитория.
- Зафиксировано, что из прикладных файлов присутствовали только служебные
  элементы (`.gitignore`, `LICENSE`, папка `docs` с ТЗ).
- Подтверждена необходимость первичного создания каркаса проекта по ТЗ.

Результат:

- Сформирован стартовый baseline для дальнейшей реализации.

### 0.2 Настройка зависимостей и инструментов

Выполнено:

- Создан `pyproject.toml` с зависимостями технологического стека проекта:
  - `langgraph`, `fastmcp`, `apimoex`, `clickhouse-connect`,
  - `feedparser`, `sqlglot`, `pydantic-settings`, `structlog`,
  - `numpy`, `scipy`, `streamlit`, `typer`,
  - `pytest`, `pytest-asyncio`,
  - `langchain-mcp-adapters`, `langchain-openai`,
  - `openai` (для OpenAI-compatible API Yandex Cloud).
- Установлен `uv` (локально в пользовательский профиль).
- Выполнена синхронизация зависимостей в локальное окружение проекта `.venv`
  через `uv sync --active --group dev`.
- Сгенерирован lock-файл `uv.lock`.
- Устранена ошибка editable-сборки пакета через добавление секции:
  `[tool.hatch.build.targets.wheel]`.

Результат:

- Среда зависимостей воспроизводима и готова к разработке.

### 0.3 Конфигурационный каркас

Выполнено:

- Создан `config/settings.py` как единый источник настроек на базе
  `pydantic-settings`.
- Добавлена фабрика `get_settings()` с кэшированием (`@lru_cache`).
- Добавлены переменные и параметры:
  - LLM: `YANDEX_CLOUD_API_KEY`, `YANDEX_CLOUD_FOLDER`,
    `YANDEX_CLOUD_MODEL`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`;
  - ClickHouse: host/port/database/user/password/timeout;
  - Оркестратор и таймауты/TTL.
- Создан `.env.example` с обязательными параметрами для Yandex Cloud и ClickHouse.
- Создан `README.md` с инструкцией быстрого старта и указанием нового LLM-провайдера.
- Проверен импорт конфигурации из активного `.venv`.

Результат:

- Конфигурационный слой соответствует требованиям единой точки настройки.

### 0.4 Формирование архитектурного каркаса проекта

Выполнено:

- Создана базовая структура директорий и файлов по ТЗ:
  - `servers/market_server`, `servers/news_server`, `servers/analytics_server`;
  - `orchestrator` и `orchestrator/nodes`;
  - `ui`;
  - `data/migrations`, `data/fixtures`;
  - `tests`.
- Добавлены стартовые модульные файлы (`__init__.py` и заглушки основных модулей).
- Добавлена начальная миграция `data/migrations/001_initial.sql`.

Результат:

- Каркас проекта подготовлен к поэтапной функциональной реализации.

### 0.5 Проверки качества на этапе

Выполнено:

- Проверены линтер-диагностики для ключевых измененных файлов (ошибок не выявлено).
- Выполнен smoke-запуск `pytest` (на текущей стадии тест-кейсы еще не добавлены,
  поэтому получен ожидаемый результат `no tests ran`).

Результат:

- Технических блокеров по переходу к этапу 1 не выявлено.

---

## Этап 1. Данные и инфраструктура хранилища

Статус: **выполнен**.

Выполнено:

- Финализирована миграция `data/migrations/001_initial.sql`:
  - добавлены доменные комментарии к полям;
  - подтверждена idempotent-логика `CREATE TABLE IF NOT EXISTS`;
  - проверены `ORDER BY` и `PARTITION BY` в соответствии с ТЗ.
- Подготовлены fixture-данные:
  - `data/fixtures/portfolio_sample.json` с 10 позициями (6 акций + 4 облигации);
  - `data/fixtures/moex_candles_sample.json` с параметрами генерации
    дневных свечей за ~2 года (520 торговых дней) для всех тикеров портфеля.
- Реализован слой доступа к данным в `servers/analytics_server/clickhouse_client.py`:
  - `ClickHouseClient` для real режима через `clickhouse-connect`;
  - `MockClickHouseClient` для dev-режима на fixture-данных;
  - фабрика `get_analytics_data_client(settings)`;
  - read-only режим и таймауты запросов;
  - логирование операций через `structlog`.
- Добавлены инфраструктурные тесты Этапа 1
  в `servers/analytics_server/tests/test_analytics.py`:
  - проверка структуры DDL и синтаксиса SQL;
  - проверка валидности `portfolio_sample.json`;
  - проверка согласованности тикеров между fixtures;
  - проверка выбора real/mock клиента фабрикой;
  - проверка генерации истории и базового SELECT в mock-режиме.
- Добавлены общие pytest-фикстуры:
  - `conftest.py` в корне проекта;
  - `tests/conftest.py` для совместимости существующей структуры.

Не выполнено:

- Полноценная интеграция с реальным ClickHouse на runtime (перейдет в Этап 2+).
- Реализация бизнес-логики риск-аналитики поверх data-layer (Этап 2).

Проверки:

- Локальный прогон `pytest` для Этапа 1: `8 passed`.

---

## Этап 2. Реализация analytics_server

Статус: **выполнен**.

Выполнено:

- Реализован `servers/analytics_server/risk_calculator.py`:
  - подготовка выровненных ценовых рядов и матрицы доходностей;
  - исторический VaR, параметрический VaR, CVaR;
  - аннуализированная волатильность;
  - Sharpe ratio с annual risk-free;
  - Max Drawdown;
  - HHI по позициям и секторам;
  - интерпретационные поля для ключевых метрик.
- Реализован `servers/analytics_server/stress_tester.py`:
  - сценарий `index_drop` (beta-оценка через OLS к рыночному прокси);
  - сценарий `rate_hike` (дюрация облигаций + чувствительные сектора акций);
  - сценарий `sector_decline` с выбором/передачей целевого сектора;
  - унифицированный формат ответа стресс-теста и сравнение с VaR(95%).
- Реализован `servers/analytics_server/server.py` (FastMCP):
  - инструменты `get_portfolio_summary`, `calculate_risk_metrics`,
    `run_stress_test`, `execute_analytics_query`;
  - docstring-описания инструментов для LLM;
  - логирование вызовов/ошибок через `structlog`;
  - перевод ошибок в `ToolError`.
- Реализована SQL-безопасность `execute_analytics_query`:
  - AST-парсинг через `sqlglot.parse_one`;
  - разрешены только `SELECT`;
  - запрет DML/DDL ключевых слов;
  - автодобавление `LIMIT 1000` при отсутствии лимита;
  - выполнение только через read-only data-client.
- Доработан `servers/analytics_server/clickhouse_client.py`:
  - добавлен метод `get_bond_details`;
  - расширен mock-режим данными `bond_details` для стресс-тестов.
- Существенно расширены тесты в `servers/analytics_server/tests/test_analytics.py`:
  - контракты `get_portfolio_summary`;
  - корректность блока риск-метрик;
  - сценарии стресс-тестов;
  - SQL-валидация и автолимит;
  - smoke асинхронных MCP-инструментов в mock-режиме.

Не выполнено:

- Интеграция с реальным production-контуром ClickHouse и калибровка формул
  под боевые исторические данные.

Проверки:

- Локальный прогон `pytest`: `15 passed`.

---

## Этап 3. Реализация market_server

Статус: **выполнен**.

Выполнено:

- Реализован `servers/market_server/moex_client.py`:
  - клиент `MoexClient` с переиспользуемой `requests.Session`;
  - валидации `ticker/board/index/date/interval/range`;
  - единые исключения `MarketDataError` и `TickerNotFoundError`;
  - in-memory TTL-кэш (`60s` для котировок, `300s` для исторических данных);
  - логирование `cache_hit/cache_miss` для диагностики;
  - нормализация ответа к контрактам ТЗ по всем инструментам.
- Реализован `servers/market_server/server.py` (FastMCP):
  - инструменты `get_stock_quote`, `get_candles`, `get_board_securities`,
    `get_index_analytics`, `get_bond_data`;
  - для инструментов добавлены docstring и MCP-аннотации
    `readOnlyHint/idempotentHint`;
  - добавлено логирование вызовов через `structlog`;
  - ожидаемые ошибки преобразуются в `ToolError`.
- Реализованы тесты `servers/market_server/tests/test_market.py`:
  - позитивные и негативные сценарии инструментов;
  - проверки валидаций дат/интервалов/board/index;
  - проверка контракта ответов;
  - тесты кэша `miss/hit/expiration`;
  - smoke-проверка MCP-инструментов в mock-режиме.

Не выполнено:

- Интеграционные проверки с реальными данными MOEX под нестабильной сетью
  и вариативными ответами ISS (выделено в дальнейшие этапы hardening).

Проверки:

- Локальный прогон `pytest`: `24 passed`.

---

## Этап 4. Реализация news_server

Статус: **выполнен**.

Выполнено:

- Реализован `servers/news_server/rss_fetcher.py`:
  - конфигурация источников `cbr/interfax/tass/rbc/smartlab/cbonds`
    с уровнями доверия `HIGH/MEDIUM/LOW`;
  - загрузка RSS через `feedparser` с таймаутом HTTP 10 секунд;
  - in-memory TTL-кэш RSS лент (`300` секунд);
  - унификация новостной записи (`title/url/published/source/source_trust/summary`);
  - фильтрация по `query` и `sources`, дедупликация `title + source`,
    сортировка по дате (desc);
  - fallback-обработка недоступных источников с `warning` логированием.
- Реализован `servers/news_server/sentiment.py`:
  - словари позитивных/негативных маркеров;
  - `classify_sentiment` (`positive/negative/neutral`);
  - `compute_sentiment_score` в диапазоне `[-1, +1]`;
  - обработка граничных случаев пустого и неоднозначного текста.
- Реализован `servers/news_server/server.py` (FastMCP):
  - инструменты `fetch_news`, `get_cb_key_rate`, `get_market_sentiment`,
    `get_macro_calendar`;
  - docstring-описания и аннотации `readOnlyHint/idempotentHint`;
  - логирование вызовов/ошибок через `structlog`;
  - преобразование ожидаемых ошибок в `ToolError`;
  - зафиксирован принцип: LLM внутри `news_server` не используется.
- Расширены тесты `servers/news_server/tests/test_news.py`:
  - sentiment эвристика и score;
  - разбор RSS на моках;
  - дедупликация и сортировка;
  - кэш `miss/hit/expiration`;
  - устойчивость к недоступному источнику;
  - smoke MCP-инструментов в mock-режиме.

Не выполнено:

- Глубокая интеграция с внешними календарями макро-событий помимо базового
  списка заседаний ЦБ (может быть усилена на этапах hardening).

Проверки:

- Локальный прогон `pytest`: `29 passed`.

---

## Этап 5. Оркестратор LangGraph

Статус: **выполнен**.

Выполнено:

- Реализован `orchestrator/state.py`:
  - `InvestmentAssistantState` с полями планирования, данных и итогового ответа;
  - тип шага плана `PlanStep`;
  - фабрика `initial_state()` для CLI/тестов/графа.
- Реализован `orchestrator/prompts.py`:
  - системный промпт planner с явными ограничениями на инструменты;
  - системный промпт summarizer с фиксированным форматом ответа.
- Реализован `orchestrator/mcp_client.py`:
  - обертка `OrchestratorMCPClient` над `MultiServerMCPClient`;
  - подключение `market/news/analytics` как stdio-процессы;
  - ленивый кэш tool-метаданных и единый `call_tool()`;
  - унифицированная ошибка `MCPClientError`.
- Реализованы узлы оркестратора:
  - `input_node`: валидация запроса, извлечение тикеров, классификация `query_type`;
  - `planner_node`: схема `PlanSchema`, first-pass генерация плана, safe fallback без LLM, `route_planner`;
  - `market_executor`: вызов market-tools, retry 1/2/4, no-retry для "тикер не найден";
  - `news_executor`: вызов news-tools, graceful degradation при серии ошибок;
  - `analytics_executor`: вызов analytics-tools, fallback-path при сбое риск-расчета;
  - `summarizer_node`: формирование структурированного `final_answer`.
- Реализован `orchestrator/graph.py`:
  - сборка `StateGraph` со всеми узлами и роутингом по ТЗ;
  - `MemorySaver` checkpointer;
  - запуск через `run_query()` с `recursion_limit` из settings.
- Реализованы тесты:
  - `tests/test_orchestrator.py` (input/planner routing/retry/degrade/fallback/интеграционный mock graph);
  - `tests/test_e2e.py` (smoke: market-only, news-only, risk-only, complex).
- Выполнена верификация:
  - локально `pytest -q`: `39 passed`;
  - lints по измененным файлам оркестратора: ошибок не выявлено.

Не выполнено:

- Интеграция planner/summarizer с боевым LLM-контуром Yandex Cloud в режиме нагрузочного теста.
- UX-слой (CLI/Streamlit) поверх `run_query()` будет закрыт в Этапе 6.

---

## Этап 6. Пользовательские интерфейсы

Статус: **выполнен**.

Выполнено:

- Реализован `ui/cli.py`:
  - Typer-команда `chat` и REPL-цикл;
  - сервисные команды `exit`, `clear`, `debug`;
  - синхронная обертка вызова `run_query` через `asyncio.run`;
  - безопасная UX-обработка ошибок без stacktrace;
  - структурированные helper-функции для smoke-тестов.
- Реализован `ui/streamlit_app.py`:
  - форма ввода запроса и кнопка запуска анализа;
  - интеграция с оркестратором через `run_query`;
  - sidebar с `portfolio_id` и выбором модели (`gpt-oss-120b` включена как UI preference);
  - отображение `final_answer`, `warnings`, fallback для пустого ответа;
  - хранение истории через `st.session_state`, spinner на время выполнения.
- Обновлен `ui/__init__.py`:
  - минимальный публичный контракт UI-пакета (`cli_app`, `run_streamlit_app`).
- Расширены smoke-тесты `tests/test_e2e.py`:
  - проверка CLI handler-команд и debug-представления;
  - проверка сборки query для Streamlit;
  - handler-level smoke для Streamlit без запуска браузера.
- Выполнена верификация:
  - `pytest -q tests/test_e2e.py`: `7 passed`;
  - `pytest -q` по проекту: `42 passed`;
  - lints по `ui/*` и обновленным тестам: ошибок не выявлено.

Не выполнено:

- Полноценный browser-level e2e Streamlit (интерактивная автоматизация UI) — вынесено в этапы hardening/демонстрации.

---

## Этап 7. Безопасность и надежность

Статус: **выполнен**.

Выполнено:

- Усилена SQL-безопасность в `servers/analytics_server/server.py`:
  - запрет multi-statement запросов;
  - обязательный single `SELECT` через AST;
  - дополнительная AST-проверка на запрещенные expression types;
  - сохранено автодобавление `LIMIT 1000`;
  - нормализованы сообщения ошибок по невалидному SQL.
- Расширены негативные SQL-тесты в `servers/analytics_server/tests/test_analytics.py`:
  - блокировка non-SELECT и многостаточных запросов;
  - проверка сохранения явного `LIMIT`;
  - безопасная обертка таймаутов в `ToolError`.
- Добавлена защита от prompt injection в оркестраторе:
  - обновлены системные промпты (`orchestrator/prompts.py`) с запретом следовать
    инструкциям из недоверенного контента;
  - добавлено экранирование `<`, `>`, `{`, `}` в `orchestrator/nodes/summarizer_node.py`.
- Введены allowlist-ограничения инструментов в executor-узлах:
  - `orchestrator/nodes/market_executor.py`;
  - `orchestrator/nodes/news_executor.py`;
  - `orchestrator/nodes/analytics_executor.py`.
- Формализован лимит ошибок оркестратора:
  - учитывается `settings.max_error_count` в planner/degradation логике.
- Закрыт аудит секретов и конфигурации:
  - подтверждено использование `config/settings.py` как единого источника;
  - подтверждено отсутствие логирования секретов;
  - в `.gitignore` оставлено игнорирование `.env`, удалено игнорирование `.env.example`
    для сохранения шаблона в репозитории.
- Расширены тесты оркестратора/интеграции:
  - тест на устойчивость planner к adversarial input;
  - тесты отклонения неразрешенных инструментов;
  - тест деградации при недоступности новостного инструмента в E2E-потоке.
- Верификация:
  - целевые тесты этапа: `35 passed`;
  - полный прогон `pytest`: `49 passed`;
  - lints по измененным файлам: ошибок не выявлено.

Не выполнено:

- Browser-level chaos/integration сценарии под реальными внешними API
  (можно расширить в Этапе 8/9).

---

## Этап 8. Тестирование и верификация

Статус: **выполнен**.

Выполнено:

- Закрыты пробелы юнит-тестов:
  - `servers/market_server/tests/test_market.py`: добавлены проверки контракта `get_candles` и
    корректного оборачивания ошибок MCP-инструмента в `ToolError`;
  - `servers/news_server/tests/test_news.py`: добавлены негативные проверки валидации входов для
    `fetch_news` и `get_macro_calendar`;
  - `servers/analytics_server/tests/test_analytics.py`: добавлены edge-case проверки для
    невалидного `confidence` и невалидного сценария stress-test с проверкой безопасного
    оборачивания ошибок.
- Усилены интеграционные тесты оркестратора в `tests/test_orchestrator.py`:
  - добавлены проверки ветвей роутинга planner (`market/news/analytics` + fallback);
  - добавлены проверки целостности `state` и наличия метрик качества в результате `run_query`.
- Полностью закрыт набор из 5 E2E-сценариев ТЗ в `tests/test_e2e.py`:
  - market-only;
  - news-only;
  - risk-only;
  - стресс-сценарий `IMOEX -20%`;
  - комплексный сценарий `портфель + новости нефтегаза`.
- Реализован сбор метрик качества:
  - добавлен модуль `orchestrator/quality_metrics.py` с расчетом полей
    `scenario_success`, `tool_selection_correct`, `response_time_sec`, `mcp_calls_count`,
    `error_count_final`;
  - интеграция в `orchestrator/graph.py` через автоматическое формирование `quality_metrics`
    на каждый `run_query`;
  - добавлен JSONL-логгер метрик и утилита сводного отчета
    `tests/quality_metrics_report.py`.
- Добавлены smoke/contract проверки для метрик в `tests/test_quality_metrics.py`.
- Обновлена конфигурация `config/settings.py` для управления метриками:
  - `quality_metrics_path`;
  - `quality_metrics_enable_file`.
- Верификация:
  - целевой прогон Этапа 8: `59 passed`;
  - полный `pytest`: `59 passed`;
  - `ReadLints` по измененным файлам: ошибок не выявлено.

Не выполнено:

- Дополнительные browser-level E2E с реальными внешними API и сетевыми сбоями
  (можно расширить в Этапе 9 при необходимости).

---

## Этап 9. Документация и финализация

Статус: **частично выполнен**.

Выполнено:

- Подготовлен детализированный план:
  `docs/Implementation_Plan_InvestmentAssistant.md`.
- Обновлен `README.md` с учетом текущего стека и провайдера LLM.
- Сформирован текущий отчет:
  `docs/Work_Report_By_Stages.md`.

Не выполнено:

- Полная эксплуатационная документация по мере реализации функционала.
- Финальные инструкции и демонстрационные сценарии для защиты.

---

## Изменения по замечанию о провайдере LLM

Выполнено:

- Заменена ориентация конфигурации с OpenAI на Yandex Cloud:
  - в `config/settings.py`,
  - в `.env.example`,
  - в `README.md`.
- Добавлены переменные:
  - `YANDEX_CLOUD_API_KEY`
  - `YANDEX_CLOUD_FOLDER`
  - `YANDEX_CLOUD_MODEL`
- Явно добавлена зависимость `openai` в `pyproject.toml`
  (для OpenAI-compatible вызовов Yandex Cloud API).

---

## Вывод

- Этапы 0-8 реализованы: включая hardening по SQL/prompt injection/allowlist/degradation и
  полное тестирование по ТЗ.
- Проект проходит актуальный набор тестов (`59 passed`), включая негативные security-сценарии
  и проверки метрик качества.
- Следующий логичный шаг: Этап 9 (документация и финализация).

