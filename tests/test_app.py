import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.store import InMemorySessionStore
from ecommerce_agent.threads.store import InMemoryThreadStore


class FakeAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
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


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def use_in_memory_stores(app) -> None:  # noqa: ANN001
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = InMemorySessionStore()


def wait_for_thread_types(client: TestClient, session_id: str, expected_types: list[str]) -> dict:
    deadline = time.monotonic() + 2.0
    last_thread: dict | None = None
    while time.monotonic() < deadline:
        last_thread = client.get(f"/api/sessions/{session_id}/thread").json()
        if [message["type"] for message in last_thread["messages"]] == expected_types:
            return last_thread
        time.sleep(0.01)
    assert last_thread is not None
    return last_thread


def test_health_reports_external_mcp_configuration() -> None:
    app = create_app(settings=make_settings())
    use_in_memory_stores(app)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["configured_mcp_servers"] == ["spring"]
    assert body["agent_ready"] is True


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
    assert spring["sales_analyst_allowed_tool_count"] == 10
    assert "inventory_query" in spring["sales_analyst_allowed_tools"]
    assert "request_approval" not in spring["sales_analyst_allowed_tools"]
    assert spring["order_manager_allowed_tool_count"] == 5
    assert spring["order_manager_allowed_tools"] == [
        "inventory_query",
        "order_query",
        "purchase_order_query",
        "request_approval",
        "supplier_query",
    ]
    assert spring["blocked_write_tools"] == [
        "order_update",
        "purchase_order_create",
        "purchase_order_receive",
    ]
    assert spring["approval_tools"] == ["request_approval"]
    assert spring["missing_expected_read_tools"] == []
    assert spring["missing_expected_order_manager_tools"] == []


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


def test_lifespan_closes_thread_store() -> None:
    class FakeThreadStore:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    store = FakeThreadStore()
    app = create_app(settings=make_settings())
    app.state.thread_store = store
    app.state.session_store = InMemorySessionStore()

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert store.closed is True


def test_lifespan_closes_session_store() -> None:
    class FakeSessionStore:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    store = FakeSessionStore()
    app = create_app(settings=make_settings())
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = store

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert store.closed is True


def test_lifespan_closes_cached_approval_clients() -> None:
    class FakeApprovalClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    approval_client = FakeApprovalClient()
    app = create_app(settings=make_settings())
    use_in_memory_stores(app)
    app.state.approval_clients = {"s1": approval_client}

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert approval_client.closed is True
    assert app.state.approval_clients == {}


def test_session_lifecycle_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.app as app_module
    from ecommerce_agent.sessions.registry import SessionRuntime

    async def fake_build_runtime(session_id: str) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id,
            agent=FakeAgent(),
            mcp_client=object(),
            sandbox=object(),
        )

    monkeypatch.setattr(app_module, "make_runtime_builder", lambda settings: fake_build_runtime)

    app = create_app(settings=make_settings())
    use_in_memory_stores(app)
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello"})
        assert response.status_code == 202
        thread = wait_for_thread_types(client, session_id, ["user", "agent_answer"])
        assert [message["type"] for message in thread["messages"]] == ["user", "agent_answer"]


def test_health_reports_components(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.health as health_module

    monkeypatch.setattr(health_module, "probe_sandbox", lambda settings: {"status": "ok"})

    app = create_app(settings=make_settings(llm_api_key="k"))
    use_in_memory_stores(app)

    with TestClient(app) as client:
        body = client.get("/health").json()

    components = body["components"]
    assert components["mongo"]["status"] == "ok"
    assert components["sandbox"]["status"] == "ok"
    assert components["model"]["status"] == "ok"
    assert components["model"]["checked"] == "config-only"


def test_health_model_unconfigured_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.health as health_module

    monkeypatch.setattr(health_module, "probe_sandbox", lambda settings: {"status": "ok"})

    app = create_app(settings=make_settings(llm_api_key=""))
    use_in_memory_stores(app)

    with TestClient(app) as client:
        body = client.get("/health").json()

    assert body["components"]["model"]["status"] == "unconfigured"


def test_app_starts_without_frontend_dist() -> None:
    app = create_app(settings=make_settings(frontend_dist_dir="/nonexistent/dist"))
    use_in_memory_stores(app)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/some/spa/route").status_code == 404


def test_spa_served_with_dist_fixture(tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(settings=make_settings(frontend_dist_dir=str(dist)))
    use_in_memory_stores(app)

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/sessions/ghost").status_code == 404
        assert "<title>console" in client.get("/").text
        assert "<title>console" in client.get("/some/spa/route").text
        assert client.get("/assets/app.js").status_code == 200
