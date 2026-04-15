"""Конфигурация structlog для MCP stdio-процессов."""

from __future__ import annotations

import sys

import structlog


def configure_structlog_for_mcp_stdio() -> None:
    """Настраивает structlog на вывод в stderr для чистого stdout JSONRPC."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.processors.add_log_level,
            structlog.processors.KeyValueRenderer(sort_keys=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
