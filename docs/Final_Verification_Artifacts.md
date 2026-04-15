## Final Verification Artifacts

Документ фиксирует итоговый набор команд верификации, ожидаемые результаты и known issues.

## 1) Команды прогона

### Полный тестовый прогон

- `pytest -q`

### Целевые прогоны

- `pytest -q tests/test_orchestrator.py tests/test_e2e.py`
- `pytest -q servers/market_server/tests/test_market.py`
- `pytest -q servers/news_server/tests/test_news.py`
- `pytest -q servers/analytics_server/tests/test_analytics.py`
- `pytest -q tests/test_quality_metrics.py`

### Проверка сводного отчета метрик

- `python tests/quality_metrics_report.py --metrics-path data/fixtures/quality_metrics.jsonl`

## 2) Ожидаемые результаты

- Все тесты проходят успешно.
- Метрики качества содержат поля:
  - `scenario_success`
  - `tool_selection_correct`
  - `response_time_sec`
  - `mcp_calls_count`
  - `error_count_final`
- Отчет `quality_metrics_report.py` формируется без ошибок.

## 2.1 Фактические результаты Этапа 9

- Изолированный dry-run (`.venv_stage9_dryrun`):
  - `uv sync --group dev` -> успешно.
  - `.venv_stage9_dryrun/Scripts/python -m pytest -q` -> `59 passed, 1 warning`.
  - `.venv_stage9_dryrun/Scripts/python tests/quality_metrics_report.py --metrics-path data/fixtures/quality_metrics.jsonl`
    -> отчет сформирован (корректный JSON, `records_count=0` для пустого файла метрик).
- Рабочее окружение проекта:
  - `pytest -q` -> `59 passed, 1 warning`.

## 3) Финальный пакет артефактов

- `README.md` (актуальные инструкции запуска и ограничения)
- `docs/Architecture_and_Data_Flows.md`
- `docs/MCP_Tool_Contracts.md`
- `docs/LangGraph_State_Flow.md`
- `docs/Defense_Demo_Scenarios.md`
- `docs/Clean_Machine_Runbook.md`
- `docs/Work_Report_By_Stages.md`

## 4) Known Issues и рекомендации

- Публичные внешние источники (MOEX/RSS) могут быть нестабильны по задержке/доступности.
- В демонстрациях рекомендуется использовать fallback-ready сценарии и mock-параметры.
- Вывод ассистента является аналитическим и не предназначен для инвестиционных решений.
