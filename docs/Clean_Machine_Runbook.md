## Clean Machine Runbook

Runbook для воспроизводимого запуска Investment Assistant на чистой машине.

## 1) Минимальные требования

- ОС: Windows 10/11, Linux, macOS
- Python: 3.11+
- Установленный `uv`
- Интернет-доступ для установки зависимостей и внешних API

## 2) Подготовка проекта

1. Клонировать репозиторий.
2. Перейти в корень проекта.
3. Подготовить env:
   - скопировать `.env.example` в `.env`;
   - заполнить ключи и параметры.
4. Установить зависимости:
   - `uv sync --group dev`

## 3) Smoke-проверка окружения

1. Проверить запуск тестов:
   - `pytest -q`
2. Проверить утилиту метрик:
   - `python tests/quality_metrics_report.py --metrics-path data/fixtures/quality_metrics.jsonl`

## 4) Запуск приложения

### MCP-серверы (3 терминала)

- `python -m servers.market_server.server`
- `python -m servers.news_server.server`
- `python -m servers.analytics_server.server`

### UI

- CLI: `python -m ui.cli chat`
- Streamlit: `streamlit run ui/streamlit_app.py`

## 5) Dry-run результаты (Этап 9)

Dry-run выполнен в изолированном окружении `.venv_stage9_dryrun`.

Проверенные команды и результаты:

1. Установка зависимостей:
   - команда: `UV_PROJECT_ENVIRONMENT=.venv_stage9_dryrun uv sync --group dev`
   - результат: успешно, зависимости установлены.
2. Полный тестовый прогон:
   - команда: `.venv_stage9_dryrun/Scripts/python -m pytest -q`
   - результат: `59 passed, 1 warning`.
3. Утилита сводного отчета метрик:
   - команда: `.venv_stage9_dryrun/Scripts/python tests/quality_metrics_report.py --metrics-path data/fixtures/quality_metrics.jsonl`
   - результат: корректный JSON-отчет сформирован.

Итог dry-run: воспроизводимость запуска подтверждена, критических блокеров не выявлено.

## 6) Типовые проблемы и решения

- **`ModuleNotFoundError`**:
  - убедиться, что `uv sync --group dev` завершен без ошибок.
- **Ошибки по `.env`**:
  - проверить наличие и заполнение обязательных переменных.
- **Проблемы с внешними API**:
  - переключиться в mock-режим (`USE_MOCK_CLICKHOUSE=true`) для локальной демонстрации.
