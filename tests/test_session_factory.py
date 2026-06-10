import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions import factory as factory_module
from ecommerce_agent.sessions.factory import RoutedSessionAgent, build_session_runtime


class FakeAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    async def astream_events(self, inputs: dict, *, config: dict, version: str):
        self.calls.append(inputs["messages"][0]["content"])
        yield {"event": "selected", "name": self.name}


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
    monkeypatch.setattr(factory_module, "build_sales_analyst", fake_build_sales_analyst)
    monkeypatch.setattr(factory_module, "build_order_manager", fake_build_order_manager)

    settings = Settings(_env_file=None, llm_api_key="k", spring_mcp_user_id="9")

    runtime = await build_session_runtime("sess-1", settings)

    assert runtime.session_id == "sess-1"
    assert isinstance(runtime.agent, RoutedSessionAgent)
    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "9"
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
async def test_routed_session_agent_sends_analysis_directly_to_analyst() -> None:
    analyst = FakeAgent("analyst")
    order_manager = FakeAgent("order-manager")
    routed = RoutedSessionAgent(analyst_agent=analyst, order_manager_agent=order_manager)

    events = [
        event
        async for event in routed.astream_events(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Forecast next month sales by category",
                    }
                ]
            },
            config={},
            version="v2",
        )
    ]

    assert events == [{"event": "selected", "name": "analyst"}]
    assert analyst.calls == ["Forecast next month sales by category"]
    assert order_manager.calls == []


@pytest.mark.asyncio
async def test_routed_session_agent_sends_restock_actions_directly_to_order_manager() -> None:
    analyst = FakeAgent("analyst")
    order_manager = FakeAgent("order-manager")
    routed = RoutedSessionAgent(analyst_agent=analyst, order_manager_agent=order_manager)

    events = [
        event
        async for event in routed.astream_events(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Create a purchase order to restock product 1",
                    }
                ]
            },
            config={},
            version="v2",
        )
    ]

    assert events == [{"event": "selected", "name": "order-manager"}]
    assert analyst.calls == []
    assert order_manager.calls == ["Create a purchase order to restock product 1"]
