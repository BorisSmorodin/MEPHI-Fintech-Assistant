## Architecture And Data Flows

Документ описывает архитектуру Investment Assistant и потоки данных между слоями.

### 1. Слои системы

- **UI Layer**: `ui/cli.py`, `ui/streamlit_app.py`
- **Orchestrator Layer**: `orchestrator/graph.py`, `orchestrator/nodes/*`, `orchestrator/state.py`
- **MCP Server Layer**:
  - `servers/market_server/server.py`
  - `servers/news_server/server.py`
  - `servers/analytics_server/server.py`
- **Data Sources / Storage**:
  - MOEX ISS API (рыночные данные)
  - RSS-источники (новости)
  - ClickHouse / mock fixtures (портфель и аналитика)

### 2. Основной поток обработки запроса

1. Пользователь отправляет запрос через CLI или Streamlit.
2. UI вызывает `orchestrator.graph.run_query(...)`.
3. Граф проходит узлы:
   - `input` -> `planner` -> `market/news/analytics executor` -> `planner` (повторно) -> `summarizer`.
4. Исполнители вызывают MCP-инструменты серверов через `orchestrator.mcp_client`.
5. Итоговое состояние агрегируется в `summarizer`, формируется `final_answer`.
6. Ответ и предупреждения возвращаются в UI.

### 3. Надежность и деградация

- Planner ограничен `max_recursion` и `max_error_count`.
- В executor-узлах используются allowlist-инструментов.
- `market_executor` применяет retry для временных ошибок.
- `news_executor` и `analytics_executor` имеют degrade/fallback-ветки.
- Summarizer санитизирует недоверенный текст (`<`, `>`, `{`, `}`).

### 4. Mermaid-диаграмма потоков

```mermaid
flowchart TD
    user[User] --> uiLayer[UI_CLI_or_Streamlit]
    uiLayer --> runQuery[run_query]
    runQuery --> inputNode[input_node]
    inputNode --> plannerNode[planner_node]
    plannerNode --> marketExec[market_executor]
    plannerNode --> newsExec[news_executor]
    plannerNode --> analyticsExec[analytics_executor]
    marketExec --> plannerNode
    newsExec --> plannerNode
    analyticsExec --> plannerNode
    plannerNode --> summarizerNode[summarizer_node]
    summarizerNode --> uiLayer

    marketExec --> moexApi[MOEX_ISS_API]
    newsExec --> rssFeeds[RSS_Sources]
    analyticsExec --> storage[ClickHouse_or_Fixtures]
```

### 5. Последовательность комплексного запроса

```mermaid
sequenceDiagram
    participant User
    participant UI
    participant Graph
    participant Planner
    participant Market
    participant News
    participant Analytics
    participant Summarizer

    User->>UI: Запрос "портфель + новости + котировки"
    UI->>Graph: run_query(user_query)
    Graph->>Planner: build_plan
    Planner->>Market: get_stock_quote/get_index_analytics
    Market-->>Graph: market_data
    Graph->>Planner: next_step
    Planner->>News: fetch_news/get_market_sentiment
    News-->>Graph: news_data
    Graph->>Planner: next_step
    Planner->>Analytics: calculate_risk_metrics/run_stress_test
    Analytics-->>Graph: portfolio_metrics
    Graph->>Summarizer: aggregate_state
    Summarizer-->>UI: final_answer + warnings
    UI-->>User: Ответ
```
