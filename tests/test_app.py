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


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class HealthyFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        assert server_name == "spring"
        return [
            FakeTool("product_query"),
            FakeTool("product_search"),
            FakeTool("order_query"),
            FakeTool("inventory_query"),
            FakeTool("inventory_low_stock"),
            FakeTool("user_query"),
            FakeTool("supplier_query"),
            FakeTool("supplier_top"),
            FakeTool("purchase_order_query"),
            FakeTool("get_statistics"),
            FakeTool("request_approval"),
            FakeTool("purchase_order_create"),
            FakeTool("purchase_order_receive"),
            FakeTool("order_update"),
        ]


class FailingFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        raise TimeoutError(f"{server_name} timed out")


def test_health_reports_external_mcp_configuration() -> None:
    app = create_app(settings=Settings())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["configured_mcp_servers"] == ["spring"]
    assert body["agent_ready"] is False


def test_mcp_health_reports_spring_tool_visibility() -> None:
    app = create_app(settings=Settings(), mcp_client=HealthyFakeMcpClient())

    with TestClient(app) as client:
        response = client.get("/health/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    spring = body["servers"]["spring"]
    assert spring["status"] == "ok"
    assert spring["tool_count"] == 14
    assert spring["agent_allowed_tool_count"] == 10
    assert "inventory_query" in spring["agent_allowed_tools"]
    assert spring["blocked_write_or_approval_tools"] == [
        "order_update",
        "purchase_order_create",
        "purchase_order_receive",
        "request_approval",
    ]
    assert spring["missing_expected_read_tools"] == []


def test_mcp_health_reports_degraded_without_starting_dependencies() -> None:
    app = create_app(settings=Settings(), mcp_client=FailingFakeMcpClient())

    with TestClient(app) as client:
        response = client.get("/health/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["servers"]["spring"]["status"] == "unavailable"
    assert "TimeoutError" in body["servers"]["spring"]["error"]


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
