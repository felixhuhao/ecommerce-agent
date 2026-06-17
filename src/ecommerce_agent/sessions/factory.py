from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    SPRING_SERVER_NAME,
    build_mcp_client,
)
from ecommerce_agent.models import get_classifier_model, get_primary_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter, Router
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from ecommerce_agent.sandbox.remote import RemoteSandboxClient
from ecommerce_agent.sessions.registry import RuntimeActor, SessionRuntime
from ecommerce_agent.specialists.providers import PROVIDERS
from ecommerce_agent.threads.history import ROUTER_HISTORY_MAX_EXCHANGES, take_last_exchanges

logger = logging.getLogger(__name__)
POLICY_DENIED_MESSAGE = (
    "This request would create an operational change, which your role is not permitted to "
    "propose. Ask an operator to perform write actions."
)


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
        selected = self.agents.get(decision.specialist)
        if selected is None:
            yield {
                "event": "on_policy_denied",
                "data": {
                    "specialist": decision.specialist,
                    "reason": "role_not_permitted",
                },
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content=POLICY_DENIED_MESSAGE)},
            }
            return
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event


def _latest_user_text(inputs: dict) -> str:
    messages = inputs.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def build_session_sandbox(settings: Settings, *, session_id: str):
    """Build the per-session sandbox backend selected by ``sandbox_backend``.

    Returns a DeepAgents ``BaseSandbox``: ``DockerSandbox`` (default) or
    ``RemoteSandboxClient`` (the sandbox executor service, design doc §6.2/§8).
    """
    backend = settings.sandbox_backend.strip().lower()
    if backend == "remote":
        if not settings.sandbox_executor_url:
            raise ValueError("sandbox_executor_url is required when sandbox_backend='remote'")
        if not settings.sandbox_executor_token:
            raise ValueError("sandbox_executor_token is required when sandbox_backend='remote'")
        return RemoteSandboxClient(
            base_url=settings.sandbox_executor_url,
            token=settings.sandbox_executor_token,
            session_id=session_id,
            timeout_seconds=settings.sandbox_execute_timeout_seconds + 10,
        )
    if backend == "docker":
        return DockerSandbox(limits_from_settings(settings), session_id=session_id)
    raise ValueError(
        f"unknown sandbox_backend {backend!r}; expected 'docker' or 'remote'"
    )


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
    # Chart rendering is first-party now. ModelScope chart MCP may still be configured
    # for diagnostics/legacy demos, but it is not part of the default runtime surface.
    viz_tools = []

    sandbox = build_session_sandbox(settings, session_id=session_id)
    model = get_primary_model(settings)

    # Runtime agents are role-shaped via provider.is_enabled: propose specialists
    # are omitted for viewers. The router registry (built below) still includes
    # every provider, so a viewer write-intent routes to the omitted specialist and
    # yields the policy-denial answer rather than rerouting to the default.
    agents: dict[str, Any] = {}
    for provider in PROVIDERS:
        if not provider.is_enabled(actor):
            continue
        agents[provider.name] = provider.build(
            model=model,
            spring_tools=spring_all_tools,
            viz_tools=viz_tools,
            backend=sandbox,
        )

    registry = build_specialist_registry()
    routed_agent = RoutedSessionAgent(
        router=ClassifierRouter(get_classifier_model(settings), registry),
        agents=agents,
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
