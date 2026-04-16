# Отчёт о бенчмарк-тестировании систем

**Дата прогона (по меткам в данных):** 2026-04-16 (UTC)  
**Репозиторий:** MEPHI Fintech Assistant — сравнение **оркестратора (LangGraph)** и **ReAct-агента** на общих демо-сценариях.

## 1. Методика

| Параметр | Значение |
|----------|----------|
| Команда | `uv run python -m ui.cli benchmark-models --system both` |
| Матрица | 6 сценариев × 2 системы (orchestrator + react) на каждую модель |
| Сценарии | `market_only`, `news_only`, `risk_assessment`, `stress_test`, `complex`, `investment_decision_intent` (каталог `ui/benchmark_catalog.py`) |
| Модель LLM | переключение через `YANDEX_CLOUD_MODEL` на время каждой строки матрицы |
| Метрики в строке JSONL | latency, `scenario_success`, `tool_selection_correct`, `mcp_calls_count`, `tokens_total`, признак `run_failed` при сбое ячейки |

## 2. Артефакты (обязательные ссылки)

### Таблица с сырыми данными (построчно по каждой ячейке)

- **[data/fixtures/benchmark_metrics.jsonl](../data/fixtures/benchmark_metrics.jsonl)** — 96 записей (JSONL: одна строка = один прогон).

### Визуализации (PNG)

Папка: **[data/fixtures/benchmark_plots/](../data/fixtures/benchmark_plots/)**

| Файл | Содержание |
|------|------------|
| [benchmark_latency_mean_by_model.png](../data/fixtures/benchmark_plots/benchmark_latency_mean_by_model.png) | Средняя задержка по моделям: orchestrator vs ReAct |
| [benchmark_tokens_mean_by_model.png](../data/fixtures/benchmark_plots/benchmark_tokens_mean_by_model.png) | Средний объём `tokens_total` |
| [benchmark_success_and_tools_by_model.png](../data/fixtures/benchmark_plots/benchmark_success_and_tools_by_model.png) | Доли `scenario_success` и `tool_selection_correct` |
| [benchmark_latency_heatmap_orchestrator.png](../data/fixtures/benchmark_plots/benchmark_latency_heatmap_orchestrator.png) | Тепловая карта latency: модель × сценарий (оркестратор) |
| [benchmark_latency_heatmap_react.png](../data/fixtures/benchmark_plots/benchmark_latency_heatmap_react.png) | То же для ReAct |

Повторная генерация графиков:

```bash
uv run python -m ui.cli benchmark-plots --metrics-path data/fixtures/benchmark_metrics.jsonl --output-dir data/fixtures/benchmark_plots
```

## 3. Сводка по всему файлу

Показатели посчитаны функцией `build_benchmark_summary_from_file` (`ui/benchmark_runner.py`).

| Показатель | Значение |
|------------|----------|
| Число записей | 96 |
| Средняя latency, с | 19.46 |
| Медиана latency, с | 8.38 |
| p95 latency, с | 81.47 |
| Доля успешных сценариев (`scenario_success`) | 0.906 |
| Доля корректного выбора инструментов | 0.865 |
| Среднее `error_count` | 0.094 |
| Среднее `tokens_total` (по всем строкам; на ReAct есть очень большие значения у отдельных моделей) | 11717 |

### По системам

| Система | Записей | Успех сценария | Корректность tools | Средняя latency, с | Среднее tokens_total |
|---------|---------|----------------|----------------------|---------------------|----------------------|
| orchestrator | 48 | 1.000 | 1.000 | 11.10 | 1807 |
| react | 48 | 0.812 | 0.729 | 27.83 | 21627 |

## 4. Сводная таблица по моделям

Каждая модель: 12 прогонов (6 сценариев × 2 системы).

| Модель | Успех сценария | Корректность tools | Средняя latency, с | Среднее tokens_total | Примечания |
|--------|----------------|----------------------|--------------------|-----------------------|------------|
| aliceai-llm/latest | 1.000 | 1.000 | 6.94 | 3526 | Стабильный прогон |
| deepseek-v32/latest | 0.833 | 1.000 | 39.59 | 16073 | Часть прогонов ReAct без финального успеха при сложных сценариях |
| gemma-3-27b-it/latest | 1.000 | 0.500 | 8.49 | 2041 | ReAct часто без вызова MCP (`mcp_calls_count`: 0) |
| gpt-oss-120b/latest | 1.000 | 1.000 | 10.94 | 38804 | Очень большой расход токенов на ReAct (в т.ч. investment) |
| gpt-oss-20b/latest | 0.917 | 0.917 | 8.20 | 2562 | Один `run_failed`: лимит 640k символов на генерацию |
| qwen3-235b-a22b-fp8/latest | 1.000 | 1.000 | 24.87 | 25874 | Высокие токены на сложных ReAct-сценариях |
| qwen3.5-35b-a3b-fp8/latest | 1.000 | 1.000 | 5.73 | 4070 | Низкая средняя latency среди полных успешных прогонов |
| yandexgpt-5-lite/latest | 0.500 | 0.500 | 50.96 | 787 | У ReAct — сбои API (`InternalServerError`), длинные ожидания |

## 5. Выводы

1. **Оркестратор** по этому прогону даёт **100%** завершённых сценариев и **100%** `tool_selection_correct` на всех моделях — план и исполнение стабильны относительно метрик в JSONL.
2. **ReAct** сильнее зависит от модели: рост latency и токенов, частичные провалы (`scenario_success`: false), инфраструктурные ошибки у **yandexgpt-5-lite**, выброс по контексту у **gpt-oss-20b** (`run_failed`, лимит размера генерации).
3. Для наглядного сравнения используются **графики** в `data/fixtures/benchmark_plots/` и **полная таблица** в `data/fixtures/benchmark_metrics.jsonl`.

---
*Документ сформирован для приложения к отчёту по практике; при обновлении JSONL пересчитайте сводку командой `benchmark-models` (с выводом summary) или используйте `build_benchmark_summary_from_file` в Python.*
