"""Общие pytest-фикстуры проекта."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    """Возвращает корневую директорию проекта."""
    return Path(__file__).resolve().parent


@pytest.fixture
def fixtures_dir(project_root: Path) -> Path:
    """Возвращает путь до каталога JSON-фикстур."""
    return project_root / "data" / "fixtures"


@pytest.fixture
def load_json():
    """Возвращает helper для загрузки JSON по пути."""

    def _loader(path: Path):
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)

    return _loader

