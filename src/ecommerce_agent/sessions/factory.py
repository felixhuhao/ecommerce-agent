from __future__ import annotations

import logging

from ecommerce_agent.agents import build_coordinator, order_manager_subagent, sales_analyst_subagent
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    build_mcp_client,
    load_modelscope_viz_tools,
    load_order_manager_tools,
    load_spring_read_tools,
)
from ecommerce_agent.models import get_primary_model
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from ecommerce_agent.sessions.registry import SessionRuntime

logger = logging.getLogger(__name__)


def build_session_sandbox(settings: Settings, *, session_id: str) -> DockerSandbox:
    return DockerSandbox(limits_from_settings(settings), session_id=session_id)


async def build_session_runtime(session_id: str, settings: Settings) -> SessionRuntime:
    """Build a per-session runtime: session-scoped MCP headers, sandbox, and agent."""
    mcp_client = build_mcp_client(
        settings,
        user_id=settings.spring_mcp_user_id,
        session_id=session_id,
    )
    spring_tools = await load_spring_read_tools(mcp_client)
    order_manager_tools = await load_order_manager_tools(mcp_client)
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
    analyst = sales_analyst_subagent(
        spring_read_tools=spring_tools,
        viz_tools=viz_tools,
    )
    order_manager = order_manager_subagent(order_manager_tools=order_manager_tools)
    agent = build_coordinator(
        model,
        sales_analyst_subagent=analyst,
        order_manager_subagent=order_manager,
        backend=sandbox,
    )
    return SessionRuntime(
        session_id=session_id,
        agent=agent,
        mcp_client=mcp_client,
        sandbox=sandbox,
    )
