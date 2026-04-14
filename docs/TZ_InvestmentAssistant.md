# Техническое задание
## Инвестиционный ассистент для мониторинга российского рынка, новостной аналитики и управления рисками портфеля

**Версия:** 1.0  
**Дата:** 12.04.2026  
**Автор:** Смородин Б.Б.  
**Научный руководитель:** [ФИО руководителя]  
**Проект:** Выпускная квалификационная работа, МИФИ

---

## 1. Общие сведения

### 1.1 Назначение системы

Разрабатываемый программный комплекс представляет собой **инвестиционного ассистента** на базе больших языковых моделей (LLM), предназначенного для частных и профессиональных инвесторов. Система обеспечивает:

- **Мониторинг российского рынка** в режиме, близком к реальному времени (акции, облигации, индексы MOEX)
- **Новостную аналитику** с агрегацией из ведущих российских финансовых источников и классификацией по влиянию на конкретные бумаги
- **Управление рисками портфеля**: расчёт VaR, CVaR, волатильности, коэффициента Шарпа, HHI концентрации, стресс-тестирование

### 1.2 Контекст разработки

Система разрабатывается в рамках дипломной работы и является прототипом (Proof of Concept), демонстрирующим архитектурный подход «LangGraph + MCP». Код должен быть чистым, хорошо документированным и пригодным для академического рецензирования.

### 1.3 Технологический стек

| Слой | Технология | Версия |
|------|-----------|--------|
| Язык | Python | ≥ 3.11 |
| LLM-оркестрация | LangGraph | ≥ 0.2 |
| MCP-фреймворк | FastMCP | ≥ 2.0 |
| LLM-провайдер | OpenAI API (gpt-4o-mini по умолчанию) | — |
| MOEX API клиент | apimoex | ≥ 1.3 |
| Аналитика | numpy, scipy, pandas | latest stable |
| Хранилище исторических данных | ClickHouse | ≥ 24.x |
| ClickHouse Python драйвер | clickhouse-connect | ≥ 0.7 |
| RSS-парсинг | feedparser | ≥ 6.0 |
| SQL-парсинг/валидация | sqlglot | latest stable |
| Веб-интерфейс | Streamlit | ≥ 1.35 |
| Тестирование | pytest + pytest-asyncio | latest stable |
| Управление зависимостями | uv | latest stable |
| Конфигурация | pydantic-settings | ≥ 2.0 |
| Логирование | structlog | latest stable |

---

## 2. Архитектура системы

### 2.1 Обзор

Система организована по трёхуровневой архитектуре:

```
┌─────────────────────────────────────────────┐
│         UI Layer (Streamlit / CLI)          │
└───────────────────┬─────────────────────────┘
                    │ Текстовый запрос
┌───────────────────▼─────────────────────────┐
│      Orchestration Layer (LangGraph)        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  input   │→ │ planner  │→ │executor  │  │
│  │  node    │  │(Supervisor│  │  nodes   │  │
│  └──────────┘  └──────────┘  └──────────┘  │
│                    │                         │
│              ┌─────▼──────┐                  │
│              │summarizer  │                  │
│              └────────────┘                  │
└───────────────────┬─────────────────────────┘
          MCP protocol (stdio)
┌──────────┬────────▼──────────┬──────────────┐
│market_   │   news_server     │analytics_    │
│server    │                   │server        │
│(MOEX ISS)│ (RSS + CBR API)   │(ClickHouse)  │
└──────────┴───────────────────┴──────────────┘
```

### 2.2 Структура проекта

```
investment_assistant/
├── .cursor/
│   └── rules/                   # Правила Cursor (копируются из ТЗ)
├── pyproject.toml               # uv / PEP 621 конфиг, все зависимости
├── .env.example                 # Шаблон переменных окружения
├── README.md
│
├── config/
│   └── settings.py              # Pydantic Settings — единый источник конфига
│
├── servers/                     # MCP-серверы (каждый — отдельный процесс)
│   ├── market_server/
│   │   ├── __init__.py
│   │   ├── server.py            # FastMCP + инструменты
│   │   ├── moex_client.py       # Обёртка над apimoex
│   │   └── tests/
│   │       └── test_market.py
│   ├── news_server/
│   │   ├── __init__.py
│   │   ├── server.py
│   │   ├── rss_fetcher.py       # Агрегация RSS
│   │   ├── sentiment.py         # Классификация тональности через LLM
│   │   └── tests/
│   │       └── test_news.py
│   └── analytics_server/
│       ├── __init__.py
│       ├── server.py
│       ├── clickhouse_client.py # Read-only ClickHouse клиент
│       ├── risk_calculator.py   # VaR, CVaR, Sharpe, HHI
│       ├── stress_tester.py     # Сценарии стресс-тестирования
│       └── tests/
│           └── test_analytics.py
│
├── orchestrator/                # LangGraph оркестратор
│   ├── __init__.py
│   ├── state.py                 # InvestmentAssistantState (TypedDict)
│   ├── nodes/
│   │   ├── __init__.py
│   │   ├── input_node.py
│   │   ├── planner_node.py
│   │   ├── market_executor.py
│   │   ├── news_executor.py
│   │   ├── analytics_executor.py
│   │   └── summarizer_node.py
│   ├── graph.py                 # StateGraph — сборка графа
│   ├── mcp_client.py            # MultiServerMCPClient wrapper
│   └── prompts.py               # Системные промпты
│
├── ui/
│   ├── cli.py                   # CLI интерфейс (typer)
│   └── streamlit_app.py         # Streamlit веб-интерфейс
│
├── data/
│   ├── migrations/              # ClickHouse DDL миграции
│   │   └── 001_initial.sql
│   └── fixtures/                # Фикстуры для тестов
│       ├── portfolio_sample.json
│       └── moex_candles_sample.json
│
└── tests/                       # Интеграционные тесты
    ├── conftest.py
    ├── test_orchestrator.py
    └── test_e2e.py
```

---

## 3. Детальные требования

### 3.1 MCP-сервер: market_server

**Назначение:** Предоставление биржевых данных Московской биржи через MOEX ISS API.

**Запуск:** `python -m servers.market_server.server` (stdio транспорт)

#### Инструменты:

**`get_stock_quote(ticker: str) → dict`**
- Возвращает текущую котировку акции с доски TQBR
- Поля: `SECID`, `LAST`, `CHANGE`, `VOLTODAY`, `BID`, `OFFER`, `UPDATETIME`
- Ошибка: `ToolError("Тикер {ticker} не найден на TQBR")` если тикер отсутствует
- Аннотации MCP: `readOnlyHint=True`, `idempotentHint=True`

**`get_candles(ticker: str, date_from: str, date_to: str, interval: int = 24) → list[dict]`**
- Свечные данные OHLCV за период
- `date_from`, `date_to` в формате `YYYY-MM-DD`
- `interval`: 1, 10, 60 (минуты), 24 (день), 7 (неделя)
- Возвращает список: `[{"begin": "...", "open": float, "high": float, "low": float, "close": float, "volume": float}]`
- Максимальный период: 5 лет (1825 дней)

**`get_board_securities(board: str = "TQBR") → list[dict]`**
- Полный список ЦБ на доске
- Допустимые boards: `TQBR` (акции), `TQCB` (корп. облигации), `TQOB` (ОФЗ)
- Возвращает: `SECID`, `SHORTNAME`, `LOTSIZE`, `PREVPRICE`

**`get_index_analytics(index: str = "IMOEX") → dict`**
- Аналитика по индексу: значение, дневное изменение, список компонентов с весами
- Допустимые индексы: `IMOEX`, `RTSI`, `RGBI`
- Возвращает: `{"value": float, "change": float, "components": [{"ticker": str, "weight": float}]}`

**`get_bond_data(ticker: str) → dict`**
- Параметры облигации
- Возвращает: `SECID`, `FACEVALUE`, `COUPONVALUE`, `ACCINT` (НКД), `YIELDATPREVWAPRICE` (YTM), `DURATION`, `MATDATE`
- Поиск по досками TQCB и TQOB

#### Требования к реализации:
- Использовать `apimoex` + `requests.Session` (переиспользование сессии)
- Обернуть все внешние вызовы в `try/except` → `ToolError`
- Реализовать простое кэширование в памяти (TTL = 60 сек для котировок, 300 сек для исторических данных)
- Логировать все входящие запросы через `structlog`

---

### 3.2 MCP-сервер: news_server

**Назначение:** Агрегация и анализ финансовых новостей из российских источников.

**Запуск:** `python -m servers.news_server.server` (stdio транспорт)

#### Источники новостей (RSS):

| Источник | URL RSS | Приоритет доверия |
|----------|---------|------------------|
| Банк России | https://www.cbr.ru/rss/RssFeed/ | HIGH |
| Интерфакс | https://www.interfax.ru/rss.asp | HIGH |
| ТАСС (экономика) | https://tass.ru/rss/v2.xml | HIGH |
| РБК | https://rbc.ru/v10/rss/v1 | MEDIUM |
| Smart-Lab | https://smart-lab.ru/rss.xml | LOW |
| Cbonds | https://cbonds.ru/rss/ | MEDIUM |

**Важно:** Приоритет доверия источника передаётся в промпт суммаризатора для корректного взвешивания информации.

#### Инструменты:

**`fetch_news(query: str, sources: list[str] = None, limit: int = 10) → list[dict]`**
- Поиск новостей по ключевым словам
- `sources`: список ключей из таблицы выше (если None — все источники)
- Каждая новость: `{"title": str, "url": str, "published": str, "source": str, "source_trust": str, "summary": str, "sentiment": str}`
- `sentiment` ∈ `{"positive", "negative", "neutral"}` — простая эвристика по ключевым словам (не LLM, чтобы не создавать рекурсию)
- Сортировка по убыванию даты
- Дедупликация по заголовку + источнику

**`get_cb_key_rate() → dict`**
- Текущая ключевая ставка ЦБ РФ
- Источник: RSS Банка России или cbr.ru открытые данные
- Возвращает: `{"rate": float, "since_date": str, "next_meeting": str}`

**`get_market_sentiment(ticker: str) → dict`**
- Агрегированная оценка настроений по тикеру за последние 7 дней
- Подсчёт по sentiment из fetch_news для данного тикера
- Возвращает: `{"ticker": str, "positive": int, "negative": int, "neutral": int, "score": float, "top_headlines": list[str]}`
- `score` = (positive - negative) / total, диапазон [-1, +1]

**`get_macro_calendar(date_from: str, date_to: str) → list[dict]`**
- Предстоящие макроэкономические события
- Источник: фиксированный список заседаний ЦБ РФ + парсинг cbr.ru
- Возвращает: `[{"date": str, "event": str, "impact": str, "previous": str}]`

#### Требования к реализации:
- Кэшировать RSS-ленты (TTL = 300 сек)
- Не использовать LLM внутри news_server (только эвристики) — избегать рекурсии
- Таймаут HTTP-запросов: 10 сек
- При недоступности источника — логировать предупреждение и продолжить с доступными

---

### 3.3 MCP-сервер: analytics_server

**Назначение:** Аналитика рисков инвестиционного портфеля с использованием ClickHouse.

**Запуск:** `python -m servers.analytics_server.server` (stdio транспорт)

#### Схема ClickHouse:

```sql
-- Таблица позиций портфеля
CREATE TABLE IF NOT EXISTS portfolios (
    portfolio_id  String,
    ticker        String,
    quantity      Float64,
    avg_price     Float64,
    sector        String,  -- нефтегаз|финансы|металлургия|IT|ритейл|другое
    instrument_type String, -- акция|облигация
    currency      String,  -- RUB|USD|EUR
    updated_at    DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (portfolio_id, ticker);

-- Таблица исторических цен
CREATE TABLE IF NOT EXISTS price_history (
    ticker        String,
    date          Date,
    open          Float64,
    high          Float64,
    low           Float64,
    close         Float64,
    volume        Float64
) ENGINE = MergeTree()
ORDER BY (ticker, date)
PARTITION BY toYYYYMM(date);

-- Таблица облигаций (доп. параметры)
CREATE TABLE IF NOT EXISTS bond_details (
    ticker        String,
    duration      Float64,  -- в годах
    coupon_rate   Float64,  -- % годовых
    ytm           Float64,  -- доходность к погашению
    maturity_date Date
) ENGINE = MergeTree()
ORDER BY ticker;
```

#### Тестовые данные (fixtures):
- Файл `data/fixtures/portfolio_sample.json` — 10 позиций: 6 акций (SBER, LKOH, GAZP, YNDX, GMKN, ROSN), 4 облигации (ОФЗ + корп.)
- Файл `data/fixtures/moex_candles_sample.json` — дневные свечи за 2 года для всех тикеров портфеля

#### Инструменты:

**`get_portfolio_summary(portfolio_id: str) → dict`**
- Полная сводка портфеля из таблицы `portfolios`
- Для расчёта текущей стоимости использует последние цены из `price_history`
- Возвращает:
```json
{
  "portfolio_id": "...",
  "total_value": float,
  "total_pnl": float,
  "total_pnl_pct": float,
  "positions": [
    {"ticker": str, "quantity": float, "avg_price": float, 
     "current_price": float, "market_value": float, 
     "pnl": float, "pnl_pct": float, "weight": float,
     "sector": str, "instrument_type": str}
  ],
  "allocation": {
    "by_sector": {"нефтегаз": float, ...},
    "by_type": {"акция": float, "облигация": float},
    "by_currency": {"RUB": float, "USD": float, "EUR": float}
  }
}
```

**`calculate_risk_metrics(portfolio_id: str, confidence: float = 0.95) → dict`**
- Расчёт метрик риска на основе `price_history` (252 торговых дня)
- Алгоритмы:
  - **Историческое VaR**: процентиль (1 - confidence) распределения исторических доходностей портфеля
  - **Параметрическое VaR**: μ − z_α × σ × √T (нормальное распределение, T=1)
  - **CVaR**: среднее по хвосту хуже VaR
  - **Волатильность**: стандартное отклонение дневных доходностей × √252 (аннуализированная)
  - **Sharpe**: (mean_return - risk_free_rate) / volatility, risk_free_rate = ключевая ставка ЦБ / 252
  - **Max Drawdown**: максимальная просадка кумулятивной доходности
  - **HHI**: Σ(weight_i²) — по позициям и по секторам
- Возвращает все метрики с единицами измерения и интерпретацией

**`run_stress_test(portfolio_id: str, scenario: str, magnitude: float) → dict`**
- Стресс-тест портфеля
- Сценарии:
  - `"index_drop"`: падение IMOEX на `magnitude`%, пересчёт каждой позиции через бета-коэффициент к IMOEX (исторический OLS за 252 дня)
  - `"rate_hike"`: рост ключевой ставки на `magnitude` п.п., переоценка облигаций: ΔP ≈ -Duration × ΔRate, акции финансового/строительного сектора -0.5 × magnitude%
  - `"sector_decline"`: снижение сектора на `magnitude`%, только позиции в данном секторе
- Возвращает: `{"scenario": str, "magnitude": float, "total_loss_rub": float, "total_loss_pct": float, "affected_positions": [...], "current_var_comparison": str}`

**`execute_analytics_query(query: str) → dict`**
- Выполнение произвольного SQL к ClickHouse
- **Безопасность (обязательно):**
  1. Парсинг AST через `sqlglot.parse_one(query)` — если не SELECT → `ToolError("Разрешены только SELECT-запросы")`
  2. Проверка отсутствия DML/DDL ключевых слов (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`)
  3. Автодобавление `LIMIT 1000` если отсутствует
  4. Таймаут выполнения 30 сек (clickhouse-connect параметр)
  5. Использовать read-only ClickHouse пользователя
- Возвращает: `{"columns": list[str], "rows": list[list], "row_count": int}`

#### Требования к реализации:
- При отсутствии ClickHouse в dev-окружении — mock-режим на JSON-фикстурах
- Все запросы к ClickHouse через `clickhouse_connect` с параметром `readonly=True`
- Расчёты через numpy/scipy (не pandas для производительности)

---

### 3.4 Оркестратор (LangGraph)

#### 3.4.1 Состояние графа

```python
from typing import TypedDict, Annotated, Optional, Any
from langgraph.graph.message import add_messages

class InvestmentAssistantState(TypedDict):
    messages:          Annotated[list, add_messages]  # История диалога
    user_query:        str                             # Исходный запрос
    query_type:        str  # "market_monitor"|"news_analysis"|"risk_assessment"|"complex"
    plan:              list[str]                       # Шаги выполнения
    current_step:      int                             # Текущий шаг
    market_data:       dict[str, Any]                  # Данные от market_server
    news_data:         list[dict]                      # Данные от news_server
    portfolio_metrics: dict[str, Any]                  # Данные от analytics_server
    final_answer:      Optional[str]                   # Итоговый ответ
    error_count:       int                             # Счётчик ошибок (max=3)
```

#### 3.4.2 Узлы графа

**input_node** (`orchestrator/nodes/input_node.py`)
- Валидация: непустая строка, длина ≤ 2000 символов
- Извлечение тикеров: регулярное выражение `[A-Z]{3,5}` среди известных тикеров MOEX
- Классификация query_type по ключевым словам:
  - `market_monitor`: "котировка", "цена", "объём", "торги", "курс", "IMOEX", "RTSI"
  - `news_analysis`: "новость", "новости", "событие", "объявление", "ставка", "ЦБ", "влияние"
  - `risk_assessment`: "риск", "портфель", "VaR", "просадка", "диверсификация", "стресс"
  - `complex`: совпадение с ≥2 категориями
- Возвращает обновлённый state с `query_type` и `user_query`

**planner_node** (`orchestrator/nodes/planner_node.py`)
- Использует LLM с structured output (Pydantic `PlanSchema`)
- При первом вызове (plan == []) — генерирует план
- При последующих вызовах — определяет следующий шаг или маршрут к summarizer
- Pydantic схема плана:
```python
class PlanStep(BaseModel):
    step_number: int
    description: str
    target_server: Literal["market_executor", "news_executor", "analytics_executor", "summarizer"]
    tool_name: str
    tool_args: dict[str, Any]

class PlanSchema(BaseModel):
    steps: list[PlanStep]
    reasoning: str
```
- Максимум итераций: 10 (recursion_limit в compile)

**market_executor** (`orchestrator/nodes/market_executor.py`)
- Вызывает инструменты market_server через MCP
- При `ToolError` → инкремент `error_count`, добавление в messages сообщение об ошибке
- Retry с exponential backoff: задержки 1s, 2s, 4s (максимум 3 попытки) для HTTP 5xx
- **НЕ** ретраит ошибки "тикер не найден" (постоянная ошибка)
- Обновляет `market_data` в state

**news_executor** (`orchestrator/nodes/news_executor.py`)
- Вызывает инструменты news_server через MCP
- При полной недоступности (error_count ≥ 3 для news): продолжает без новостей, добавляет предупреждение
- Обновляет `news_data` в state

**analytics_executor** (`orchestrator/nodes/analytics_executor.py`)
- Вызывает инструменты analytics_server через MCP
- При ошибке calculate_risk_metrics — пробует упрощённый расчёт (только по доступным данным)
- Обновляет `portfolio_metrics` в state

**summarizer_node** (`orchestrator/nodes/summarizer_node.py`)
- Принимает все накопленные данные: market_data, news_data, portfolio_metrics
- Промпт включает:
  - Данные с источниками и временными метками
  - Весовые коэффициенты доверия источников новостей (HIGH/MEDIUM/LOW)
  - Инструкцию форматировать ответ структурировано: заголовок, ключевые факты, риски, итог
- Заполняет `final_answer` в state

#### 3.4.3 Сборка графа

```python
# orchestrator/graph.py — схема сборки
builder = StateGraph(InvestmentAssistantState)
builder.add_node("input", input_node)
builder.add_node("planner", planner_node)
builder.add_node("market_executor", market_executor)
builder.add_node("news_executor", news_executor)
builder.add_node("analytics_executor", analytics_executor)
builder.add_node("summarizer", summarizer_node)

builder.add_edge(START, "input")
builder.add_edge("input", "planner")
builder.add_conditional_edges("planner", route_planner, {
    "market_executor": "market_executor",
    "news_executor": "news_executor",
    "analytics_executor": "analytics_executor",
    "summarizer": "summarizer",
})
builder.add_edge("market_executor", "planner")
builder.add_edge("news_executor", "planner")
builder.add_edge("analytics_executor", "planner")
builder.add_edge("summarizer", END)

graph = builder.compile(checkpointer=MemorySaver())
```

#### 3.4.4 Подключение MCP-серверов

- Использовать `langchain_mcp_adapters.MultiServerMCPClient`
- Конфигурация серверов в `config/settings.py`
- Каждый сервер запускается как подпроцесс через stdio
- Загрузка tools при инициализации приложения (не при каждом запросе)

---

### 3.5 Конфигурация

**Файл:** `config/settings.py` (Pydantic BaseSettings)

```python
class Settings(BaseSettings):
    # LLM
    openai_api_key: str
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.1
    
    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_database: str = "investment"
    clickhouse_user: str = "readonly_user"
    clickhouse_password: str
    
    # Серверы
    market_server_timeout: int = 30
    news_server_cache_ttl: int = 300
    
    # Оркестратор
    max_recursion: int = 10
    max_error_count: int = 3
    
    # Dev
    use_mock_clickhouse: bool = False  # True → fixtures вместо CH
    
    model_config = SettingsConfigDict(env_file=".env")
```

**`.env.example`:**
```
OPENAI_API_KEY=sk-...
CLICKHOUSE_PASSWORD=readonly_password
USE_MOCK_CLICKHOUSE=true
```

---

### 3.6 Пользовательский интерфейс

#### CLI (`ui/cli.py`)
- Команда: `python -m ui.cli chat`
- REPL-режим: читает запрос, выводит ответ, повторяет
- Команды: `exit`, `clear` (сбросить state), `debug` (показать граф состояния)

#### Streamlit (`ui/streamlit_app.py`)
- Запуск: `streamlit run ui/streamlit_app.py`
- Элементы:
  - Поле ввода запроса
  - Отображение ответа (markdown)
  - Боковая панель: выбор portfolio_id, модель LLM
  - История диалога (session state)
  - Индикатор выполнения (spinner) во время обработки

---

## 4. Требования к безопасности

### 4.1 SQL-инъекции (execute_analytics_query)
- AST-валидация через sqlglot ОБЯЗАТЕЛЬНА перед исполнением
- Использовать read-only пользователя ClickHouse (`readonly = 1` в профиле)
- Таймаут выполнения 30 сек
- Максимум 1000 строк в результате

### 4.2 Prompt Injection
- Новостные данные экранировать перед передачей в LLM: заменять `<`, `>`, `{`, `}` на безопасные эквиваленты
- Системный промпт планировщика НЕ может быть переопределён пользовательским вводом
- Максимальная длина пользовательского запроса: 2000 символов

### 4.3 Ключи API
- Только через переменные окружения (`.env`)
- Никогда не логировать значения секретов
- Файл `.env` в `.gitignore`

---

## 5. Тестирование

### 5.1 Юнит-тесты (pytest)

| Модуль | Что тестировать | Файл |
|--------|----------------|------|
| market_server | get_stock_quote (mock apimoex), ToolError при неверном тикере | `servers/market_server/tests/test_market.py` |
| news_server | fetch_news (mock feedparser), sentiment эвристика | `servers/news_server/tests/test_news.py` |
| analytics_server | VaR расчёт (синтетические данные), SQL-валидация | `servers/analytics_server/tests/test_analytics.py` |
| orchestrator | input_node классификация, planner routing | `tests/test_orchestrator.py` |

### 5.2 Интеграционные тесты

- `test_e2e.py`: 5 сценариев (см. Главу 4 ТЗ)
  1. "Покажи котировку SBER" → только market_server
  2. "Последние новости по Газпрому" → только news_server
  3. "Оцени риск портфеля demo_portfolio" → analytics + market
  4. "Стресс-тест: IMOEX -20%" → analytics_server
  5. "Оцени портфель с учётом новостей нефтегаза" → все три сервера

### 5.3 Метрики качества (для Главы 4 диплома)

Необходимо логировать для каждого запроса:
- `scenario_success`: bool (завершился без исключения)
- `tool_selection_correct`: bool (вызваны правильные серверы)
- `response_time_sec`: float (время от запроса до ответа)
- `mcp_calls_count`: int
- `error_count_final`: int

---

## 6. Порядок разработки (рекомендуемый)

1. **Инфраструктура**: `pyproject.toml`, `config/settings.py`, `.env.example`, `data/migrations/001_initial.sql`
2. **Тестовые данные**: `data/fixtures/portfolio_sample.json`, `data/fixtures/moex_candles_sample.json`
3. **analytics_server** (самый независимый, работает на fixtures): `risk_calculator.py` → `stress_tester.py` → `server.py` → тесты
4. **market_server**: `moex_client.py` → `server.py` → тесты
5. **news_server**: `rss_fetcher.py` → `sentiment.py` → `server.py` → тесты
6. **orchestrator state + graph**: `state.py` → `graph.py` (скелет)
7. **orchestrator nodes**: `input_node.py` → `planner_node.py` → executor nodes → `summarizer_node.py`
8. **MCP интеграция**: `mcp_client.py`, подключение серверов к графу
9. **UI**: `cli.py` → `streamlit_app.py`
10. **Интеграционные тесты**: `test_e2e.py`

---

## 7. Ограничения и допущения

- В рамках прототипа MOEX ISS API используется без аутентификации (бесплатный публичный доступ, данные могут быть задержаны на 15–30 мин)
- Тональность новостей определяется эвристически (ключевые слова), без отдельной LLM-модели классификации
- Данные ClickHouse инициализируются из фикстур (для воспроизводимости тестов)
- Стресс-тестирование использует упрощённые параметрические модели (не Monte Carlo)
- Система является прототипом и не предназначена для реальных инвестиционных решений
