from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings


class FakeAgent:
    async def astream_events(self, inputs: dict, version: str) -> AsyncIterator[dict]:
        assert inputs["messages"][0]["content"] == "hello"
        assert version == "v2"
        yield {"event": "on_tool_start", "name": "inventory_query", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Inventory looks healthy.")},
        }
        yield {"event": "on_tool_end", "name": "inventory_query", "data": {}}


def test_health_reports_external_mcp_configuration() -> None:
    app = create_app(settings=Settings())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["configured_mcp_servers"] == ["spring"]
    assert body["agent_ready"] is False


def test_chat_stream_maps_agent_events_to_sse_frames() -> None:
    app = create_app(settings=Settings(), agent=FakeAgent())

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: tool" in body
    assert '"name": "inventory_query"' in body
    assert "event: token" in body
    assert "Inventory looks healthy." in body
    assert "event: done" in body
