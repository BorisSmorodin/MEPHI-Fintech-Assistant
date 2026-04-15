"""Пакет пользовательских интерфейсов."""

from ui.cli import app as cli_app
from ui.streamlit_app import main as run_streamlit_app

__all__ = ["cli_app", "run_streamlit_app"]

