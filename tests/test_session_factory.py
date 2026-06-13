import logging

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.routing.router import RouteDecision
from ecommerce_agent.sessions import factory as factory_module
from ecommerce_agent.sessions.factory import (
    POLICY_DENIED_MESSAGE,
    RoutedSessionAgent,
    build_session_runtime,
)
from ecommerce_agent.sessions.registry import RuntimeActor


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def astream_events(self, inputs: dict, *, config: dict, version: str):
        self.calls.append(inputs["messages"][0]["content"])
        yield {"event": "selected", "name": self.name}


class StubRouter:
    def __init__(self, specialist: str) -> None:
        self._specialist = specialist
        self.seen: list[str] = []
        self.seen_history: list = []

    async def route(self, message: str, *, history=()) -> RouteDecision:
        self.seen.append(message)
        self.seen_history = list(history)
        return RouteDecision(self._specialist, "classifier", "r")


def _agents() -> dict[str, FakeAgent]:
    return {
        "sales-analyst": FakeAgent("analyst"),
        "order-manager": FakeAgent("order-manager"),
    }


@pytest.mark.asyncio
async def test_build_session_runtime_wires_session_scoped_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeMcpClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_tools(self, *, server_name: str):
            self.calls.append(server_name)
            return [
                FakeTool("product_query"),
                FakeTool("order_query"),
                FakeTool("request_approval"),
            ]

    mcp_client = FakeMcpClient()

    def fake_build_mcp_client(settings, *, user_id, session_id):
        captured["user_id"] = user_id
        captured["session_id"] = session_id
        return mcp_client

    def fake_build_sandbox(settings, *, session_id):
        captured["sandbox_session_id"] = session_id
        return object()

    def fake_build_stage_tool(*, spring_read_tools, backend):
        captured["stage_tool_inputs"] = [tool.name for tool in spring_read_tools]
        captured["stage_tool_backend"] = backend
        return FakeTool("stage_sales_analysis_inputs")

    def fake_build_sales_analyst(model, *, spring_read_tools, staging_tools, viz_tools, backend):
        captured["direct_analyst_tools"] = [tool.name for tool in spring_read_tools]
        captured["direct_staging_tools"] = [tool.name for tool in staging_tools]
        captured["direct_viz_tools"] = [tool.name for tool in viz_tools]
        captured["direct_analyst_backend"] = backend
        return FakeAgent("ANALYST")

    def fake_build_order_manager(model, *, order_manager_tools, backend):
        captured["direct_order_manager_tools"] = [tool.name for tool in order_manager_tools]
        captured["direct_order_manager_backend"] = backend
        return FakeAgent("ORDER_MANAGER")

    monkeypatch.setattr(factory_module, "build_mcp_client", fake_build_mcp_client)
    monkeypatch.setattr(factory_module, "build_session_sandbox", fake_build_sandbox)
    monkeypatch.setattr(factory_module, "build_sales_analysis_staging_tool", fake_build_stage_tool)
    monkeypatch.setattr(factory_module, "get_primary_model", lambda settings: object())
    monkeypatch.setattr(factory_module, "get_classifier_model", lambda settings: object())
    monkeypatch.setattr(factory_module, "build_sales_analyst", fake_build_sales_analyst)
    monkeypatch.setattr(factory_module, "build_order_manager", fake_build_order_manager)

    settings = Settings(_env_file=None, llm_api_key="k", spring_mcp_user_id="9")

    actor = RuntimeActor(user_id="alice", spring_user_id=42, can_propose=True)

    runtime = await build_session_runtime("sess-1", settings, actor)

    assert runtime.session_id == "sess-1"
    assert isinstance(runtime.agent, RoutedSessionAgent)
    assert runtime.owner_id == "alice"
    assert runtime.spring_user_id == 42
    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "42"
    assert captured["sandbox_session_id"] == "sess-1"
    assert captured["stage_tool_inputs"] == ["product_query", "order_query"]
    assert captured["stage_tool_backend"] is captured["direct_analyst_backend"]
    assert captured["direct_analyst_tools"] == ["product_query", "order_query"]
    assert captured["direct_staging_tools"] == ["stage_sales_analysis_inputs"]
    assert captured["direct_order_manager_tools"] == [
        "product_query",
        "order_query",
        "request_approval",
    ]
    assert captured["direct_order_manager_backend"] is captured["direct_analyst_backend"]
    assert mcp_client.calls == ["spring"]


@pytest.mark.asyncio
async def test_routed_session_agent_delegates_to_router_choice(caplog) -> None:
    caplog.set_level(logging.INFO, logger=factory_module.__name__)
    agents = _agents()
    routed = RoutedSessionAgent(
        router=StubRouter("order-manager"),
        agents=agents,
        default_specialist="sales-analyst",
    )

    events = [
        e
        async for e in routed.astream_events(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "create a purchase order",
                    }
                ]
            },
            config={},
            version="v2",
        )
    ]

    assert events[0] == {
        "event": "on_route_decision",
        "data": {"specialist": "order-manager", "source": "classifier", "reason": "r"},
    }
    assert {"event": "selected", "name": "order-manager"} in events
    assert agents["order-manager"].calls == ["create a purchase order"]
    assert agents["sales-analyst"].calls == []
    assert "route decision: specialist=order-manager source=classifier reason=r" in caplog.text


@pytest.mark.asyncio
async def test_routed_agent_passes_recent_history_to_router() -> None:
    router = StubRouter("order-manager")
    routed = RoutedSessionAgent(
        router=router,
        agents=_agents(),
        default_specialist="sales-analyst",
    )

    messages = [
        {"role": "user", "content": "how are electronics selling?"},
        {"role": "assistant", "content": "Down 12% this month."},
        {"role": "user", "content": "restock the worst performer"},
    ]

    _ = [e async for e in routed.astream_events({"messages": messages}, config={}, version="v2")]

    assert router.seen == ["restock the worst performer"]
    assert {"role": "assistant", "content": "Down 12% this month."} in router.seen_history
    assert {"role": "user", "content": "restock the worst performer"} not in router.seen_history


@pytest.mark.asyncio
async def test_routed_session_agent_denies_unknown_specialist() -> None:
    agents = _agents()
    routed = RoutedSessionAgent(
        router=StubRouter("ghost"),
        agents=agents,
        default_specialist="sales-analyst",
    )

    events = [
        event
        async for event in routed.astream_events(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "hi",
                    }
                ]
            },
            config={},
            version="v2",
        )
    ]

    assert events[1] == {
        "event": "on_policy_denied",
        "data": {"specialist": "ghost", "reason": "role_not_permitted"},
    }
    assert events[2]["data"]["chunk"].content == POLICY_DENIED_MESSAGE
    assert agents["sales-analyst"].calls == []
