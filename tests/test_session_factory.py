import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions import factory as factory_module
from ecommerce_agent.sessions.factory import build_session_runtime


@pytest.mark.asyncio
async def test_build_session_runtime_wires_session_scoped_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    def fake_build_mcp_client(settings, *, user_id, session_id):
        captured["user_id"] = user_id
        captured["session_id"] = session_id
        return object()

    async def fake_load_spring_read_tools(client):
        return [FakeTool("order_query")]

    async def fake_load_order_manager_tools(client):
        return [FakeTool("request_approval")]

    def fake_build_sandbox(settings, *, session_id):
        captured["sandbox_session_id"] = session_id
        return object()

    def fake_sales_analyst_subagent(*, spring_read_tools, viz_tools):
        captured["analyst_tools"] = [tool.name for tool in spring_read_tools]
        captured["viz_tools"] = [tool.name for tool in viz_tools]
        return {"name": "sales-analyst"}

    def fake_order_manager_subagent(*, order_manager_tools):
        captured["order_manager_tools"] = [tool.name for tool in order_manager_tools]
        return {"name": "order-manager"}

    def fake_build_coordinator(model, *, sales_analyst_subagent, order_manager_subagent, backend):
        captured["subagents"] = [sales_analyst_subagent["name"], order_manager_subagent["name"]]
        return "COORDINATOR"

    monkeypatch.setattr(factory_module, "build_mcp_client", fake_build_mcp_client)
    monkeypatch.setattr(factory_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(
        factory_module, "load_order_manager_tools", fake_load_order_manager_tools
    )
    monkeypatch.setattr(factory_module, "build_session_sandbox", fake_build_sandbox)
    monkeypatch.setattr(factory_module, "get_primary_model", lambda settings: object())
    monkeypatch.setattr(factory_module, "sales_analyst_subagent", fake_sales_analyst_subagent)
    monkeypatch.setattr(factory_module, "order_manager_subagent", fake_order_manager_subagent)
    monkeypatch.setattr(factory_module, "build_coordinator", fake_build_coordinator)

    settings = Settings(_env_file=None, llm_api_key="k", spring_mcp_user_id="9")

    runtime = await build_session_runtime("sess-1", settings)

    assert runtime.session_id == "sess-1"
    assert runtime.agent == "COORDINATOR"
    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "9"
    assert captured["sandbox_session_id"] == "sess-1"
    assert captured["analyst_tools"] == ["order_query"]
    assert captured["order_manager_tools"] == ["request_approval"]
    assert captured["subagents"] == ["sales-analyst", "order-manager"]
