"""Streamlit-интерфейс инвестиционного ассистента."""

from __future__ import annotations

import asyncio
from typing import Any

import streamlit as st
import structlog

from config.settings import get_settings
from orchestrator.graph import run_query

log = structlog.get_logger()


def build_effective_query(user_query: str, portfolio_id: str) -> str:
    """Добавляет контекст портфеля в запрос при необходимости."""
    normalized_query = user_query.strip()
    normalized_portfolio = portfolio_id.strip()
    if not normalized_portfolio:
        return normalized_query
    if normalized_portfolio.lower() in normalized_query.lower():
        return normalized_query
    return f"{normalized_query} (portfolio_id: {normalized_portfolio})"


async def execute_streamlit_query(
    user_query: str,
    portfolio_id: str,
    selected_model: str,
) -> dict[str, Any]:
    """Выполняет запрос оркестратора для Streamlit-страницы."""
    effective_query = build_effective_query(user_query, portfolio_id)
    result = await run_query(effective_query)

    warnings = list(result.get("warnings", []))
    if selected_model:
        warnings.append(f"UI model preference: {selected_model}")
    result["warnings"] = warnings
    return result


def init_session_state() -> None:
    """Инициализирует session_state для истории и диагностики."""
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []
    if "last_state" not in st.session_state:
        st.session_state["last_state"] = None


def render_history() -> None:
    """Отрисовывает историю запросов в интерфейсе."""
    for row in st.session_state["chat_history"]:
        st.markdown(f"**Запрос:** {row['query']}")
        st.markdown(row["answer"])
        if row.get("warnings"):
            for warning in row["warnings"]:
                st.warning(warning)
        st.divider()


def main() -> None:
    """Запускает Streamlit UI инвестиционного ассистента."""
    settings = get_settings()
    st.set_page_config(page_title="Investment Assistant", layout="wide")
    st.title("Investment Assistant")
    st.caption("UI-слой для оркестратора LangGraph")

    init_session_state()

    with st.sidebar:
        st.header("Параметры")
        portfolio_id = st.text_input("Portfolio ID", value="demo_portfolio")
        selected_model = st.selectbox(
            "LLM Model",
            options=[settings.yandex_cloud_model, "gpt-oss-120b", settings.llm_model],
            index=0,
        )
        st.caption("Выбор модели сохраняется как UI preference.")

    user_query = st.text_area("Введите запрос", placeholder="Например: Оцени риск портфеля demo_portfolio")
    run_clicked = st.button("Запустить анализ", type="primary")

    if run_clicked:
        try:
            if not user_query.strip():
                st.error("Запрос не может быть пустым.")
            elif len(user_query.strip()) > 2000:
                st.error("Длина запроса не должна превышать 2000 символов.")
            else:
                with st.spinner("Выполняется анализ..."):
                    state = asyncio.run(
                        execute_streamlit_query(
                            user_query=user_query,
                            portfolio_id=portfolio_id,
                            selected_model=selected_model,
                        )
                    )
                st.session_state["last_state"] = state
                final_answer = str(state.get("final_answer") or "").strip()
                warnings = list(state.get("warnings", []))
                if final_answer:
                    st.markdown(final_answer)
                else:
                    st.warning("Оркестратор не вернул итоговый ответ. Попробуйте уточнить запрос.")
                for warning in warnings:
                    st.warning(warning)
                st.session_state["chat_history"].append(
                    {
                        "query": user_query.strip(),
                        "answer": final_answer or "Пустой ответ.",
                        "warnings": warnings,
                    }
                )
        except ValueError as error:
            st.error(f"Ошибка валидации: {error}")
        except Exception as error:
            log.error("streamlit_query_failed", error=str(error))
            st.error("Произошла ошибка при обработке запроса.")

    if st.session_state["chat_history"]:
        st.subheader("История")
        render_history()


if __name__ == "__main__":
    main()

