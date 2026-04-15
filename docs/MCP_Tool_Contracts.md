## MCP Tool Contracts

Документ фиксирует контракты MCP-инструментов для трех серверов: market, news, analytics.

## 1) Market Server

Источник: `servers/market_server/server.py`.

### `get_stock_quote(ticker: str) -> dict`

- **Назначение**: получить текущую котировку с доски `TQBR`.
- **Вход**:
  - `ticker` (обязательный, строка).
- **Ключевые поля ответа**:
  - `SECID`, `LAST`, `CHANGE`, `VOLTODAY`, `BID`, `OFFER`, `UPDATETIME`.
- **Типовые ошибки**:
  - тикер не найден -> `ToolError`.
  - ошибка внешнего источника -> `ToolError`.

### `get_candles(ticker: str, date_from: str, date_to: str, interval: int = 24) -> list[dict]`

- **Назначение**: получить OHLCV-свечи.
- **Вход**:
  - `ticker`, `date_from`, `date_to`, `interval`.
- **Ключевые поля свечи**:
  - `begin`, `open`, `high`, `low`, `close`, `volume`.
- **Типовые ошибки**:
  - невалидный диапазон дат/интервал -> `ToolError`.

### `get_board_securities(board: str = "TQBR") -> list[dict]`

- **Назначение**: список бумаг по выбранной доске.
- **Вход**:
  - `board` из набора `TQBR|TQCB|TQOB`.
- **Ключевые поля**:
  - `SECID`, `SHORTNAME`, `LOTSIZE`, `PREVPRICE`.

### `get_index_analytics(index: str = "IMOEX") -> dict`

- **Назначение**: аналитика индекса.
- **Вход**:
  - `index` из набора `IMOEX|RTSI|RGBI`.
- **Ключевые поля**:
  - `value`, `change`, `components[{ticker, weight}]`.

### `get_bond_data(ticker: str) -> dict`

- **Назначение**: параметры облигации из `TQCB/TQOB`.
- **Вход**:
  - `ticker`.
- **Ключевые поля**:
  - `SECID`, `FACEVALUE`, `COUPONVALUE`, `ACCINT`, `YIELDATPREVWAPRICE`, `DURATION`, `MATDATE`.

## 2) News Server

Источник: `servers/news_server/server.py`.

### `fetch_news(query: str, sources: list[str] | None = None, limit: int = 10) -> list[dict]`

- **Назначение**: поиск новостей + trust + sentiment.
- **Вход**:
  - `query` (обязательный, непустой),
  - `sources` (опционально),
  - `limit` (по умолчанию 10).
- **Ключевые поля новости**:
  - `title`, `url`, `published`, `source`, `source_trust`, `summary`, `sentiment`.
- **Типовые ошибки**:
  - пустой `query` -> `ToolError`.
  - ошибка RSS источника/парсинга -> `ToolError`.

### `get_cb_key_rate() -> dict`

- **Назначение**: текущая ключевая ставка и ближайшее заседание ЦБ.
- **Ключевые поля**:
  - `rate`, `since_date`, `next_meeting`.

### `get_market_sentiment(ticker: str) -> dict`

- **Назначение**: агрегированная тональность за 7 дней.
- **Вход**:
  - `ticker` (обязательный).
- **Ключевые поля**:
  - `ticker`, `positive`, `negative`, `neutral`, `score`, `top_headlines`.

### `get_macro_calendar(date_from: str, date_to: str) -> list[dict]`

- **Назначение**: календарь макрособытий в диапазоне.
- **Вход**:
  - `date_from`, `date_to` в формате `YYYY-MM-DD`.
- **Ключевые поля события**:
  - `date`, `event`, `impact`, `previous`.

## 3) Analytics Server

Источник: `servers/analytics_server/server.py`.

### `get_portfolio_summary(portfolio_id: str) -> dict`

- **Назначение**: сводка портфеля.
- **Вход**:
  - `portfolio_id`.
- **Ключевые поля**:
  - `portfolio_id`, `total_value`, `total_pnl`, `positions`, `allocation`.

### `calculate_risk_metrics(portfolio_id: str, confidence: float = 0.95) -> dict`

- **Назначение**: риск-метрики (VaR, CVaR, volatility, Sharpe, MDD, HHI).
- **Вход**:
  - `portfolio_id`, `confidence`.
- **Ключевые поля**:
  - `var_historical`, `var_parametric`, `cvar`, `volatility`, `sharpe`, `max_drawdown`, `hhi`.

### `run_stress_test(portfolio_id: str, scenario: str, magnitude: float, target_sector: str | None = None) -> dict`

- **Назначение**: стресс-тесты `index_drop|rate_hike|sector_decline`.
- **Вход**:
  - `portfolio_id`, `scenario`, `magnitude`, `target_sector`.
- **Ключевые поля**:
  - `scenario`, `total_loss_rub`, `total_loss_pct`, `affected_positions`, `current_var_comparison`.

### `execute_analytics_query(query: str) -> dict`

- **Назначение**: безопасный read-only SQL.
- **Вход**:
  - `query` (single SELECT).
- **Ключевые поля**:
  - `columns`, `rows`, `row_count`.
- **Ограничения безопасности**:
  - только `SELECT`,
  - запрет multi-statement и DDL/DML,
  - авто `LIMIT 1000`.

## 4) Общие гарантии оркестратора

- Вызовы инструментов ограничены allowlist в executor-узлах.
- При частичных отказах включается безопасная деградация.
- Ошибки инструментов нормализуются в `ToolError` и учитываются в `error_count`.
