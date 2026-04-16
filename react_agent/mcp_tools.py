"""Загрузка MCP-инструментов через общий клиент оркестратора."""

from __future__ import annotations

from collections.abc import Sequence

import structlog
from langchain_core.tools import BaseTool

from orchestrator.mcp_client import OrchestratorMCPClient, get_mcp_client

log = structlog.get_logger()


async def load_mcp_tools(client: OrchestratorMCPClient | None = None) -> list[BaseTool]:
    """Возвращает плоский список инструментов market, news, analytics."""
    mcp = client or get_mcp_client()
    tools: Sequence[BaseTool] = await mcp.list_tools()
    names = [t.name for t in tools]
    log.info("react_agent_tools_loaded", tools_count=len(tools), tool_names=sorted(names))
    return list(tools)
