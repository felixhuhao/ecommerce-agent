from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import ecommerce_agent.api.chat as chat_module
from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings


class FakeAgent:
    def __init__(self, *, expected_recursion_limit: int | None = None) -> None:
        self.expected_recursion_limit = expected_recursion_limit

    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        assert inputs["messages"][0]["content"] == "hello"
        if self.expected_recursion_limit is not None:
            assert config == {"recursion_limit": self.expected_recursion_limit}
        assert version == "v2"
        yield {"event": "on_tool_start", "name": "inventory_query", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Inventory looks healthy.")},
        }
        yield {"event": "on_tool_end", "name": "inventory_query", "data": {}}


class ExplodingFakeAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        raise RuntimeError("secret provider stack trace")
        yield


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def spring_mcp_tools() -> list[FakeTool]:
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


class HealthyFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        assert server_name == "spring"
        return spring_mcp_tools()


class HealthySpringAndModelscopeFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        if server_name == "spring":
            return spring_mcp_tools()
        if server_name == "modelscope":
            return [
                FakeTool("generate_line_chart"),
                FakeTool("generate_bar_chart"),
                FakeTool("generate_column_chart"),
                FakeTool("generate_area_chart"),
            ]
        raise AssertionError(f"unexpected server: {server_name}")


class FailingFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        raise TimeoutError(f"{server_name} timed out")


class BuildableFakeMcpClient:
    pass


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_health_reports_external_mcp_configuration() -> None:
    app = create_app(settings=make_settings())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["configured_mcp_servers"] == ["spring"]
    assert body["agent_ready"] is False


def test_mcp_health_reports_spring_tool_visibility() -> None:
    app = create_app(settings=make_settings(), mcp_client=HealthyFakeMcpClient())

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


def test_mcp_health_reports_modelscope_viz_tool_visibility() -> None:
    app = create_app(
        settings=make_settings(modelscope_mcp_url="http://modelscope.example/mcp"),
        mcp_client=HealthySpringAndModelscopeFakeMcpClient(),
    )

    with TestClient(app) as client:
        response = client.get("/health/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    modelscope = body["servers"]["modelscope"]
    assert modelscope["status"] == "ok"
    assert modelscope["tool_count"] == 4
    assert modelscope["agent_allowed_tool_count"] == 3
    assert modelscope["agent_allowed_tools"] == [
        "generate_bar_chart",
        "generate_column_chart",
        "generate_line_chart",
    ]
    assert modelscope["missing_expected_viz_tools"] == []


def test_mcp_health_reports_degraded_without_starting_dependencies() -> None:
    app = create_app(settings=make_settings(), mcp_client=FailingFakeMcpClient())

    with TestClient(app) as client:
        response = client.get("/health/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["servers"]["spring"]["status"] == "unavailable"
    assert "TimeoutError" in body["servers"]["spring"]["error"]


def test_chat_stream_maps_agent_events_to_sse_frames() -> None:
    settings = make_settings(agent_recursion_limit=123)
    app = create_app(
        settings=settings,
        agent=FakeAgent(expected_recursion_limit=settings.agent_recursion_limit),
    )

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat/stream", json={"message": "  hello  "}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: tool" in body
    assert '"name": "inventory_query"' in body
    assert "event: token" in body
    assert "Inventory looks healthy." in body
    assert "event: done" in body
    record = app.state.last_trace
    assert record is not None
    assert record.tool_names() == ["inventory_query"]
    assert "Inventory looks healthy." in record.answer


def test_chat_stream_rejects_blank_message() -> None:
    app = create_app(settings=make_settings(), agent=FakeAgent())

    with TestClient(app) as client:
        response = client.post("/api/chat/stream", json={"message": "   "})

    assert response.status_code == 422


def test_chat_stream_error_message_does_not_leak_internal_exception() -> None:
    app = create_app(settings=make_settings(), agent=ExplodingFakeAgent())

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: error" in body
    assert chat_module.STREAM_ERROR_MESSAGE in body
    assert "secret provider stack trace" not in body
    assert app.state.last_trace is not None
    assert app.state.last_trace.duration_ms is not None


def test_health_reports_unknown_tool_count_for_injected_agent() -> None:
    app = create_app(settings=make_settings(), agent=FakeAgent())

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["agent_ready"] is True
    assert body["tool_count"] is None


def test_lifespan_builds_and_closes_sandbox_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.app as app_module

    events = {"built": 0, "closed": 0}

    class FakeBackend:
        def close(self) -> None:
            events["closed"] += 1

    def fake_build_backend(settings: Settings) -> FakeBackend:
        events["built"] += 1
        return FakeBackend()

    monkeypatch.setattr(app_module, "build_sandbox_backend", fake_build_backend)

    app = create_app(settings=make_settings())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert events["built"] == 1
        assert app.state.sandbox_backend is not None

    assert events["closed"] == 1


def test_chat_stream_lazily_builds_analyst_with_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"spring": 0, "viz": 0, "model": 0, "analyst": 0}

    async def fake_load_spring_read_tools(mcp_client: BuildableFakeMcpClient) -> list[FakeTool]:
        assert isinstance(mcp_client, BuildableFakeMcpClient)
        calls["spring"] += 1
        return [FakeTool("order_query")]

    async def fake_load_modelscope_viz_tools(mcp_client: BuildableFakeMcpClient) -> list[FakeTool]:
        assert isinstance(mcp_client, BuildableFakeMcpClient)
        calls["viz"] += 1
        return [FakeTool("generate_line_chart")]

    def fake_get_primary_model(settings: Settings) -> object:
        assert settings.llm_api_key == "test-key"
        calls["model"] += 1
        return object()

    def fake_build_sales_analyst(
        model: object,
        *,
        spring_read_tools: list[FakeTool],
        viz_tools: list[FakeTool],
        backend: object,
    ) -> FakeAgent:
        assert model is not None
        assert backend is not None
        assert [tool.name for tool in spring_read_tools] == ["order_query"]
        assert [tool.name for tool in viz_tools] == ["generate_line_chart"]
        calls["analyst"] += 1
        return FakeAgent()

    monkeypatch.setattr(chat_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(chat_module, "load_modelscope_viz_tools", fake_load_modelscope_viz_tools)
    monkeypatch.setattr(chat_module, "get_primary_model", fake_get_primary_model)
    monkeypatch.setattr(chat_module, "build_sales_analyst", fake_build_sales_analyst)

    settings = make_settings(
        llm_api_key="test-key",
        modelscope_mcp_url="http://modelscope.example/mcp",
    )
    app = create_app(
        settings=settings,
        mcp_client=BuildableFakeMcpClient(),
    )
    app.state.sandbox_backend = object()

    with TestClient(app) as client:
        for _ in range(2):
            with client.stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
                body = "".join(response.iter_text())
            assert response.status_code == 200
            assert "Inventory looks healthy." in body

        health = client.get("/health").json()

    assert calls == {"spring": 1, "viz": 1, "model": 1, "analyst": 1}
    assert health["agent_ready"] is True
    assert health["tool_count"] == 2


def test_chat_stream_falls_back_when_modelscope_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = {"spring": 0, "viz": 0, "model": 0, "analyst": 0}

    async def fake_load_spring_read_tools(mcp_client: BuildableFakeMcpClient) -> list[FakeTool]:
        calls["spring"] += 1
        return [FakeTool("order_query")]

    async def fake_load_modelscope_viz_tools(mcp_client: BuildableFakeMcpClient) -> list[FakeTool]:
        calls["viz"] += 1
        raise TimeoutError("modelscope down")

    def fake_get_primary_model(settings: Settings) -> object:
        calls["model"] += 1
        return object()

    def fake_build_sales_analyst(
        model: object,
        *,
        spring_read_tools: list[FakeTool],
        viz_tools: list[FakeTool],
        backend: object,
    ) -> FakeAgent:
        calls["analyst"] += 1
        assert [tool.name for tool in spring_read_tools] == ["order_query"]
        assert viz_tools == []
        return FakeAgent()

    monkeypatch.setattr(chat_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(chat_module, "load_modelscope_viz_tools", fake_load_modelscope_viz_tools)
    monkeypatch.setattr(chat_module, "get_primary_model", fake_get_primary_model)
    monkeypatch.setattr(chat_module, "build_sales_analyst", fake_build_sales_analyst)

    settings = make_settings(
        llm_api_key="test-key",
        modelscope_mcp_url="http://modelscope.example/mcp",
    )
    app = create_app(settings=settings, mcp_client=BuildableFakeMcpClient())
    app.state.sandbox_backend = object()

    with TestClient(app) as client:
        with caplog.at_level("WARNING", logger="ecommerce_agent.api.chat"):
            with client.stream("POST", "/api/chat/stream", json={"message": "hello"}) as response:
                body = "".join(response.iter_text())
        health = client.get("/health").json()

    assert response.status_code == 200
    assert "Inventory looks healthy." in body
    assert calls == {"spring": 1, "viz": 1, "model": 1, "analyst": 1}
    assert health["tool_count"] == 1
    assert "ModelScope MCP unavailable" in caplog.text
