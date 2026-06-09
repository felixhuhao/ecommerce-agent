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

    def fake_build_sandbox(settings, *, session_id):
        captured["sandbox_session_id"] = session_id
        return object()

    def fake_build_sales_analyst(model, *, spring_read_tools, viz_tools, backend):
        captured["tools"] = [tool.name for tool in spring_read_tools]
        return "ANALYST"

    monkeypatch.setattr(factory_module, "build_mcp_client", fake_build_mcp_client)
    monkeypatch.setattr(factory_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(factory_module, "build_session_sandbox", fake_build_sandbox)
    monkeypatch.setattr(factory_module, "get_primary_model", lambda settings: object())
    monkeypatch.setattr(factory_module, "build_sales_analyst", fake_build_sales_analyst)

    settings = Settings(_env_file=None, llm_api_key="k", spring_mcp_user_id="9")

    runtime = await build_session_runtime("sess-1", settings)

    assert runtime.session_id == "sess-1"
    assert runtime.agent == "ANALYST"
    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "9"
    assert captured["sandbox_session_id"] == "sess-1"
    assert captured["tools"] == ["order_query"]
