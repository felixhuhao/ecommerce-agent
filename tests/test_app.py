import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import _evict_approval_clients_for_sessions, create_app
from ecommerce_agent.audit.query import InMemoryAuditStore
from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Actor, Role, User
from ecommerce_agent.auth.passwords import hash_password
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import MODELSCOPE_VIZ_TOOLS
from ecommerce_agent.monitoring.store import InMemoryAlertStore
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.sessions.store import InMemorySessionStore
from ecommerce_agent.threads.store import InMemoryThreadStore
from ecommerce_agent.trace.store import InMemoryTraceStore


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
            return [FakeTool(name) for name in sorted(MODELSCOPE_VIZ_TOOLS)]
        raise AssertionError(f"unexpected server: {server_name}")


class FailingFakeMcpClient:
    async def get_tools(self, server_name: str) -> list[FakeTool]:
        raise TimeoutError(f"{server_name} timed out")


def _local_mongo_settings() -> dict[str, object]:
    """Pick up MONGO_URL/MONGO_DB from the local .env so the app/health tests run
    against the authenticated dev Mongo (slice-11 B1). No credentials live in source."""
    overrides: dict[str, object] = {}
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("MONGO_URL="):
                overrides["mongo_url"] = line.split("=", 1)[1].strip()
            elif line.startswith("MONGO_DB="):
                overrides["mongo_db"] = line.split("=", 1)[1].strip()
    return overrides


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**_local_mongo_settings(), **overrides})


TEST_USER = User(
    user_id="alice",
    username="alice",
    password_hash=hash_password("pw"),
    role=Role.OPERATOR,
    spring_user_id=7,
    created_at="2026-06-13T00:00:00+00:00",
)
TEST_ACTOR = Actor.from_user(TEST_USER)


def use_in_memory_stores(app) -> None:  # noqa: ANN001
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = InMemorySessionStore()
    app.state.trace_store = InMemoryTraceStore()
    use_in_memory_auth_stores(app)


def use_in_memory_auth_stores(app) -> None:  # noqa: ANN001
    if app.state.thread_store is None:
        app.state.thread_store = InMemoryThreadStore()
    if app.state.session_store is None:
        app.state.session_store = InMemorySessionStore()
    if app.state.trace_store is None:
        app.state.trace_store = InMemoryTraceStore()
    app.state.user_store = InMemoryUserStore()
    app.state.user_store._by_id[TEST_USER.user_id] = TEST_USER
    app.state.user_store._by_username[TEST_USER.username] = TEST_USER.user_id
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.audit_store = InMemoryAuditStore()
    app.state.alert_store = InMemoryAlertStore()
    app.dependency_overrides[current_actor] = lambda: TEST_ACTOR


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
    use_in_memory_auth_stores(app)

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
    assert spring["order_manager_allowed_tool_count"] == 2
    assert spring["order_manager_allowed_tools"] == ["order_query", "request_approval"]
    assert spring["purchasing_allowed_tool_count"] == 5
    assert spring["purchasing_allowed_tools"] == [
        "product_search",
        "purchase_order_query",
        "request_approval",
        "supplier_query",
        "supplier_top",
    ]
    assert spring["inventory_allowed_tool_count"] == 3
    assert spring["inventory_allowed_tools"] == [
        "inventory_low_stock",
        "inventory_query",
        "product_search",
    ]
    assert spring["customer_insights_allowed_tool_count"] == 3
    assert spring["customer_insights_allowed_tools"] == [
        "get_statistics",
        "order_query",
        "user_query",
    ]
    assert spring["blocked_write_tools"] == [
        "order_update",
        "purchase_order_create",
        "purchase_order_receive",
    ]
    assert spring["approval_tools"] == ["request_approval"]
    assert spring["missing_expected_read_tools"] == []
    assert spring["missing_expected_order_manager_tools"] == []
    assert spring["missing_expected_purchasing_tools"] == []
    assert spring["missing_expected_inventory_tools"] == []
    assert spring["missing_expected_customer_insights_tools"] == []


def test_mcp_health_reports_modelscope_viz_tool_visibility() -> None:
    app = create_app(
        settings=make_settings(modelscope_mcp_url="http://modelscope.example/mcp"),
        mcp_client=HealthySpringAndModelscopeFakeMcpClient(),
    )
    use_in_memory_auth_stores(app)

    with TestClient(app) as client:
        response = client.get("/health/mcp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    modelscope = body["servers"]["modelscope"]
    assert modelscope["status"] == "ok"
    assert modelscope["tool_count"] == len(MODELSCOPE_VIZ_TOOLS)
    assert modelscope["runtime_enabled"] is False
    assert "create_chart_spec" in modelscope["note"]
    assert modelscope["optional_legacy_viz_tool_count"] == len(MODELSCOPE_VIZ_TOOLS)
    assert modelscope["optional_legacy_viz_tools"] == sorted(MODELSCOPE_VIZ_TOOLS)
    assert modelscope["missing_optional_legacy_viz_tools"] == []


def test_mcp_health_reports_degraded_without_starting_dependencies() -> None:
    app = create_app(settings=make_settings(), mcp_client=FailingFakeMcpClient())
    use_in_memory_auth_stores(app)

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
    use_in_memory_auth_stores(app)

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
    use_in_memory_auth_stores(app)

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
    app.state.approval_clients = {("s1", "alice"): approval_client}

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert approval_client.closed is True
    assert app.state.approval_clients == {}


@pytest.mark.asyncio
async def test_evicts_approval_clients_for_reaped_sessions() -> None:
    class FakeApprovalClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    stale_alice = FakeApprovalClient()
    stale_bob = FakeApprovalClient()
    active = FakeApprovalClient()
    clients = {
        ("stale", "alice"): stale_alice,
        ("stale", "bob"): stale_bob,
        ("active", "alice"): active,
    }

    await _evict_approval_clients_for_sessions(clients, ["stale"])

    assert clients == {("active", "alice"): active}
    assert stale_alice.closed is True
    assert stale_bob.closed is True
    assert active.closed is False


def test_lifespan_shares_default_mongo_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.app as app_module

    class FakeCollection:
        def __init__(self, name: str) -> None:
            self.name = name
            self.indexes: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def create_index(self, *args: object, **kwargs: object) -> str:
            self.indexes.append((args, kwargs))
            return f"{self.name}_idx"

    class FakeDatabase:
        def __init__(self) -> None:
            self.collections: dict[str, FakeCollection] = {}

        def __getitem__(self, name: str) -> FakeCollection:
            if name not in self.collections:
                self.collections[name] = FakeCollection(name)
            return self.collections[name]

    class FakeMongoClient:
        def __init__(self, url: str) -> None:
            self.url = url
            self.closed_count = 0
            self.databases: dict[str, FakeDatabase] = {}
            self.admin = SimpleNamespace(command=self.command)

        def __getitem__(self, name: str) -> FakeDatabase:
            if name not in self.databases:
                self.databases[name] = FakeDatabase()
            return self.databases[name]

        async def command(self, name: str) -> dict[str, int]:
            assert name == "ping"
            return {"ok": 1}

        def close(self) -> None:
            self.closed_count += 1

    clients: list[FakeMongoClient] = []

    def fake_client(url: str) -> FakeMongoClient:
        client = FakeMongoClient(url)
        clients.append(client)
        return client

    monkeypatch.setattr(app_module, "AsyncIOMotorClient", fake_client)
    app = create_app(
        settings=make_settings(mongo_url="mongodb://mongo"),
        mcp_client=HealthyFakeMcpClient(),
    )

    with TestClient(app) as client:
        assert client.get("/health/mcp").status_code == 200
        shared = clients[0]
        assert app.state.thread_store._client is shared
        assert app.state.session_store._client is shared
        assert app.state.trace_store._client is shared
        assert app.state.user_store._client is shared
        assert app.state.login_session_store._client is shared
        assert app.state.audit_store._client is shared

    assert len(clients) == 1
    assert clients[0].closed_count == 1


def test_session_lifecycle_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.app as app_module
    from ecommerce_agent.sessions.registry import SessionRuntime

    async def fake_build_runtime(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id,
            agent=FakeAgent(),
            mcp_client=object(),
            sandbox=object(),
            owner_id=actor.user_id,
            spring_user_id=actor.spring_user_id,
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

    monkeypatch.setattr(
        health_module,
        "probe_sandbox",
        lambda settings: {"status": "ok", "backend": "remote"},
    )

    app = create_app(settings=make_settings(llm_api_key="k"))
    use_in_memory_stores(app)

    with TestClient(app) as client:
        body = client.get("/health").json()

    components = body["components"]
    assert components["mongo"]["status"] == "ok"
    assert components["sandbox"]["status"] == "ok"
    assert components["sandbox"]["backend"] == "remote"
    assert components["model"]["status"] == "ok"
    assert components["model"]["checked"] == "config-only"


def test_sandbox_health_probes_remote_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.health as health_module

    seen: dict[str, object] = {}

    class Response:
        def raise_for_status(self) -> None:
            seen["raised"] = False

    def fake_get(url: str, *, timeout: float) -> Response:
        seen["url"] = url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(health_module.httpx, "get", fake_get)

    status = health_module.probe_sandbox(
        make_settings(
            sandbox_backend="remote",
            sandbox_executor_url="http://executor:8000/",
        )
    )

    assert status == {"status": "ok", "backend": "remote"}
    assert seen["url"] == "http://executor:8000/health"
    assert seen["timeout"] == 1.0


def test_sandbox_health_reports_remote_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.health as health_module

    def fake_get(url: str, *, timeout: float):  # noqa: ANN001
        raise TimeoutError("nope")

    monkeypatch.setattr(health_module.httpx, "get", fake_get)

    status = health_module.probe_sandbox(
        make_settings(
            sandbox_backend="remote",
            sandbox_executor_url="http://executor:8000",
        )
    )

    assert status == {"status": "unavailable", "backend": "remote"}


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
