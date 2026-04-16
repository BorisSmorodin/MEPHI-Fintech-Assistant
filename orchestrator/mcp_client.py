"""Клиент подключения к MCP-серверам."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
import structlog

from config.settings import Settings, get_settings

log = structlog.get_logger()


class MCPClientError(Exception):
    """Ошибка вызова инструмента через MCP."""


class OrchestratorMCPClient:
    """Обертка над MultiServerMCPClient с кэшем инструментов."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        project_root = Path(__file__).resolve().parents[1]
        server_env = dict(os.environ)
        server_env["PYTHONIOENCODING"] = "utf-8"
        server_env["PYTHONUTF8"] = "1"
        self._client = MultiServerMCPClient(
            {
                "market": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "servers.market_server.server"],
                    "cwd": project_root,
                    "env": server_env,
                },
                "news": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "servers.news_server.server"],
                    "cwd": project_root,
                    "env": server_env,
                },
                "analytics": {
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": ["-m", "servers.analytics_server.server"],
                    "cwd": project_root,
                    "env": server_env,
                },
            }
        )
        self._tools: dict[str, BaseTool] = {}
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def ensure_initialized(self) -> None:
        """Ленивая инициализация каталога инструментов."""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return
            market_tools = await self._client.get_tools(server_name="market")
            news_tools = await self._client.get_tools(server_name="news")
            analytics_tools = await self._client.get_tools(server_name="analytics")
            all_tools = [*market_tools, *news_tools, *analytics_tools]
            self._tools = {tool.name: tool for tool in all_tools}
            self._initialized = True

    async def list_tools(self) -> list[BaseTool]:
        """Возвращает все MCP-инструменты (market, news, analytics) в стабильном порядке."""
        await self.ensure_initialized()
        return sorted(self._tools.values(), key=lambda t: t.name)

    async def call_tool(self, tool_name: str, tool_args: dict[str, Any]) -> Any:
        """Выполняет вызов инструмента по имени и аргументам."""
        await self.ensure_initialized()
        tool = self._tools.get(tool_name)
        if tool is None:
            raise MCPClientError(f"Инструмент {tool_name} не найден в MCP-клиенте.")
        try:
            log.info("mcp_client_tool_call_started", tool_name=tool_name, tool_args=tool_args)
            result = await tool.ainvoke(tool_args)
            log.info("mcp_client_tool_call_succeeded", tool_name=tool_name)
            return result
        except Exception as error:
            log.warning("mcp_client_tool_call_failed", tool_name=tool_name, tool_args=tool_args, error=str(error))
            raise MCPClientError(str(error)) from error


_mcp_client: OrchestratorMCPClient | None = None


def get_mcp_client() -> OrchestratorMCPClient:
    """Возвращает singleton MCP-клиента для оркестратора."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = OrchestratorMCPClient()
    return _mcp_client

