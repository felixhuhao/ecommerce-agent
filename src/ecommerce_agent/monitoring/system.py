from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any

from ecommerce_agent.agents import build_monitor_cause_agent
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    SPRING_SERVER_NAME,
    build_mcp_client,
    filter_spring_read_tools,
)
from ecommerce_agent.models import get_summary_model
from ecommerce_agent.monitoring.checks import build_default_checks
from ecommerce_agent.monitoring.reader import McpMonitorReader


@dataclass
class MonitorRuntime:
    reader: McpMonitorReader
    checks: list[Any]
    cause_agent: Any | None
    mcp_client: Any

    async def close(self) -> None:
        await _close_mcp_client(self.mcp_client)


async def build_monitor_runtime(settings: Settings) -> MonitorRuntime:
    mcp_client = build_mcp_client(
        settings,
        user_id=settings.monitor_spring_user_id,
        session_id=settings.monitor_spring_session_id,
    )
    try:
        spring_tools = await mcp_client.get_tools(server_name=SPRING_SERVER_NAME)
        read_tools = filter_spring_read_tools(spring_tools)
        cause_agent = None
        if settings.monitor_cause_enabled and settings.llm_api_key:
            cause_agent = build_monitor_cause_agent(
                get_summary_model(settings),
                spring_read_tools=read_tools,
            )
        return MonitorRuntime(
            reader=McpMonitorReader(read_tools),
            checks=build_default_checks(settings),
            cause_agent=cause_agent,
            mcp_client=mcp_client,
        )
    except Exception:
        await _close_mcp_client(mcp_client)
        raise


async def _close_mcp_client(mcp_client: Any) -> None:
    close = getattr(mcp_client, "aclose", None) or getattr(mcp_client, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result
