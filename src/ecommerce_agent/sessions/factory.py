from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from ecommerce_agent.agents import (
    build_order_manager,
    build_sales_analyst,
)
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    SPRING_SERVER_NAME,
    build_mcp_client,
    filter_order_manager_tools,
    filter_spring_read_tools,
    load_modelscope_viz_tools,
)
from ecommerce_agent.models import get_primary_model
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from ecommerce_agent.sessions.registry import SessionRuntime
from ecommerce_agent.tools.staging import build_sales_analysis_staging_tool

logger = logging.getLogger(__name__)


_ORDER_MANAGER_KEYWORDS = (
    "approval",
    "approve",
    "create purchase order",
    "purchase order",
    "receive purchase",
    "receive po",
    "replenish",
    "restock",
    "update order",
    "order status",
)


class RoutedSessionAgent:
    """Route single-specialist turns directly, leaving the coordinator as a future seam."""

    def __init__(self, *, analyst_agent: Any, order_manager_agent: Any) -> None:
        self.analyst_agent = analyst_agent
        self.order_manager_agent = order_manager_agent

    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        text = _latest_user_text(inputs)
        selected = self.order_manager_agent if _needs_order_manager(text) else self.analyst_agent
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event


def _latest_user_text(inputs: dict) -> str:
    messages = inputs.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def _needs_order_manager(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _ORDER_MANAGER_KEYWORDS)


def build_session_sandbox(settings: Settings, *, session_id: str) -> DockerSandbox:
    return DockerSandbox(limits_from_settings(settings), session_id=session_id)


async def build_session_runtime(session_id: str, settings: Settings) -> SessionRuntime:
    """Build a per-session runtime: session-scoped MCP headers, sandbox, and agent."""
    mcp_client = build_mcp_client(
        settings,
        user_id=settings.spring_mcp_user_id,
        session_id=session_id,
    )
    spring_all_tools = await mcp_client.get_tools(server_name=SPRING_SERVER_NAME)
    spring_tools = filter_spring_read_tools(spring_all_tools)
    order_manager_tools = filter_order_manager_tools(spring_all_tools)
    if settings.modelscope_mcp_url:
        try:
            viz_tools = await load_modelscope_viz_tools(mcp_client)
        except Exception:
            logger.warning(
                "ModelScope MCP unavailable; continuing without viz tools",
                exc_info=True,
            )
            viz_tools = []
    else:
        viz_tools = []

    sandbox = build_session_sandbox(settings, session_id=session_id)
    model = get_primary_model(settings)
    staging_tools = [
        build_sales_analysis_staging_tool(
            spring_read_tools=spring_tools,
            backend=sandbox,
        )
    ]
    analyst_agent = build_sales_analyst(
        model,
        spring_read_tools=spring_tools,
        staging_tools=staging_tools,
        viz_tools=viz_tools,
        backend=sandbox,
    )
    order_manager_agent = build_order_manager(
        model,
        order_manager_tools=order_manager_tools,
        backend=sandbox,
    )
    routed_agent = RoutedSessionAgent(
        analyst_agent=analyst_agent,
        order_manager_agent=order_manager_agent,
    )
    return SessionRuntime(
        session_id=session_id,
        agent=routed_agent,
        mcp_client=mcp_client,
        sandbox=sandbox,
    )
