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
from ecommerce_agent.models import get_classifier_model, get_primary_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter, Router
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from ecommerce_agent.sessions.registry import RuntimeActor, SessionRuntime
from ecommerce_agent.threads.history import ROUTER_HISTORY_MAX_EXCHANGES, take_last_exchanges
from ecommerce_agent.tools.staging import build_sales_analysis_staging_tool

logger = logging.getLogger(__name__)


class RoutedSessionAgent:
    """Route each turn via a Router, then delegate to the chosen specialist agent."""

    def __init__(self, *, router: Router, agents: dict[str, Any], default_specialist: str) -> None:
        self.router = router
        self.agents = agents
        self.default_specialist = default_specialist

    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        messages = inputs.get("messages") or []
        text = _latest_user_text(inputs)
        history = take_last_exchanges(list(messages[:-1]), ROUTER_HISTORY_MAX_EXCHANGES)
        decision = await self.router.route(text, history=history)
        logger.info(
            "route decision: specialist=%s source=%s reason=%s",
            decision.specialist,
            decision.source,
            decision.reason,
        )
        yield {
            "event": "on_route_decision",
            "data": {
                "specialist": decision.specialist,
                "source": decision.source,
                "reason": decision.reason,
            },
        }
        selected = self.agents.get(decision.specialist) or self.agents[self.default_specialist]
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event


def _latest_user_text(inputs: dict) -> str:
    messages = inputs.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def build_session_sandbox(settings: Settings, *, session_id: str) -> DockerSandbox:
    return DockerSandbox(limits_from_settings(settings), session_id=session_id)


async def build_session_runtime(
    session_id: str,
    settings: Settings,
    actor: RuntimeActor,
) -> SessionRuntime:
    """Build a per-session runtime bound to `actor` (Spring id + proposal capability)."""
    mcp_client = build_mcp_client(
        settings,
        user_id=str(actor.spring_user_id),
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
    registry = build_specialist_registry()
    routed_agent = RoutedSessionAgent(
        router=ClassifierRouter(get_classifier_model(settings), registry),
        agents={"sales-analyst": analyst_agent, "order-manager": order_manager_agent},
        default_specialist=registry.default.name,
    )
    return SessionRuntime(
        session_id=session_id,
        agent=routed_agent,
        mcp_client=mcp_client,
        sandbox=sandbox,
        owner_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
    )
