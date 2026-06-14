from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions import factory as factory
from ecommerce_agent.sessions.factory import (
    POLICY_DENIED_MESSAGE,
    RoutedSessionAgent,
)
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.specialists import providers as providers_module


class SpyAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.called = False

    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        self.called = True
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content=self.name)},
        }


@pytest.mark.asyncio
async def test_router_denies_unavailable_specialist_without_delegating() -> None:
    analyst = SpyAgent("analyst")

    class StubRouter:
        async def route(self, text: str, *, history=()):
            return type(
                "Decision",
                (),
                {"specialist": "order-manager", "source": "test", "reason": "write"},
            )()

    routed = RoutedSessionAgent(
        router=StubRouter(),
        agents={"sales-analyst": analyst},
        default_specialist="sales-analyst",
    )

    events = [
        event
        async for event in routed.astream_events(
            {"messages": [{"role": "user", "content": "make a PO"}]},
            config={},
            version="v2",
        )
    ]

    assert analyst.called is False
    assert events[1] == {
        "event": "on_policy_denied",
        "data": {"specialist": "order-manager", "reason": "role_not_permitted"},
    }
    assert events[2]["event"] == "on_chat_model_stream"
    assert events[2]["data"]["chunk"].content == POLICY_DENIED_MESSAGE


@pytest.mark.asyncio
async def test_viewer_runtime_does_not_build_order_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMcpClient:
        async def get_tools(self, *, server_name: str) -> list:
            return []

    monkeypatch.setattr(factory, "build_mcp_client", lambda *args, **kwargs: FakeMcpClient())
    monkeypatch.setattr(factory, "build_session_sandbox", lambda *args, **kwargs: object())
    monkeypatch.setattr(factory, "get_primary_model", lambda settings: object())
    monkeypatch.setattr(factory, "get_classifier_model", lambda settings: object())
    monkeypatch.setattr(
        providers_module, "build_sales_analysis_staging_tool", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        providers_module, "build_sales_analyst", lambda *args, **kwargs: SpyAgent("analyst")
    )

    def fail_build_order_manager(*args, **kwargs):
        raise AssertionError("viewer runtime must not build order-manager")

    monkeypatch.setattr(providers_module, "build_order_manager", fail_build_order_manager)

    runtime = await factory.build_session_runtime(
        "s1",
        Settings(_env_file=None),
        RuntimeActor(user_id="viewer", spring_user_id=9, can_propose=False),
    )

    assert "order-manager" not in runtime.agent.agents
