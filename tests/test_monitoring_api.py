import asyncio
import json
from types import SimpleNamespace

import anyio
from fastapi.testclient import TestClient

import ecommerce_agent.api.app as app_module
from ecommerce_agent.api.app import create_app
from ecommerce_agent.api.monitoring import _alert_events, run_monitor_from_app
from ecommerce_agent.audit.query import InMemoryAuditStore
from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Actor, Role
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings
from ecommerce_agent.monitoring.bus import AlertBus
from ecommerce_agent.monitoring.checks import LowStockCheck
from ecommerce_agent.monitoring.models import Alert, AlertGrounding
from ecommerce_agent.monitoring.reader import InMemoryMonitorReader
from ecommerce_agent.monitoring.store import InMemoryAlertStore
from ecommerce_agent.sessions.store import InMemorySessionStore
from ecommerce_agent.threads.store import InMemoryThreadStore
from ecommerce_agent.trace.store import InMemoryTraceStore

OPERATOR = Actor(
    user_id="op1",
    username="op1",
    role=Role.OPERATOR,
    spring_user_id=1,
)
VIEWER = Actor(
    user_id="viewer1",
    username="viewer1",
    role=Role.VIEWER,
    spring_user_id=2,
)


class Runtime:
    def __init__(self) -> None:
        self.reader = InMemoryMonitorReader(
            low_stock_rows=[{"sku": "SKU-9", "name": "Power Bank", "quantity": 12}]
        )
        self.checks = [LowStockCheck(threshold=50)]
        self.cause_agent = None


def app_with_alerts(
    actor: Actor = OPERATOR,
    settings: Settings | None = None,
):  # noqa: ANN201
    app = create_app(settings=settings or Settings(_env_file=None))
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = InMemorySessionStore()
    app.state.trace_store = InMemoryTraceStore()
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.audit_store = InMemoryAuditStore()
    app.state.alert_store = InMemoryAlertStore()
    app.state.alert_bus = AlertBus()
    app.state.monitor_runtime_factory = Runtime
    app.dependency_overrides[current_actor] = lambda: actor
    return app


def test_alert_routes_are_operator_only() -> None:
    with TestClient(app_with_alerts(VIEWER)) as client:
        response = client.get("/api/alerts")

    assert response.status_code == 403


def test_manual_monitor_run_creates_alert() -> None:
    with TestClient(app_with_alerts()) as client:
        response = client.post("/api/monitor/run")
        alerts_response = client.get("/api/alerts?status=open")

    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    body = alerts_response.json()
    assert body["alerts"][0]["title"] == "Low stock: Power Bank"
    assert body["alerts"][0]["grounding"]["sources"][0]["tool_name"] == "inventory_low_stock"


def test_acknowledge_alert_updates_status() -> None:
    app = app_with_alerts()
    alert = Alert(
        check_name="low_stock",
        dedupe_key="low_stock:SKU-9",
        title="Low stock: SKU-9",
        metric="inventory",
        grounding=AlertGrounding(authority="authoritative"),
    )
    anyio.run(app.state.alert_store.create, alert)

    with TestClient(app) as client:
        response = client.post(f"/api/alerts/{alert.alert_id}/acknowledge")

    assert response.status_code == 200
    assert response.json()["alert"]["status"] == "acknowledged"
    assert response.json()["alert"]["acknowledged_by"] == "op1"


async def test_manual_run_reports_already_running_when_lock_is_held() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    app = SimpleNamespace(state=SimpleNamespace(monitor_run_lock=lock))

    result = await run_monitor_from_app(app)

    assert result == {"status": "already_running"}
    lock.release()


async def test_alert_sse_emits_published_alert() -> None:
    bus = AlertBus()

    async def is_connected() -> bool:
        return False

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(alert_bus=bus)),
        is_disconnected=is_connected,
    )
    events = _alert_events(request)
    next_event = asyncio.create_task(anext(events))
    await asyncio.sleep(0)

    bus.publish({"event": "alert.created", "alert": {"alert_id": "a1"}})
    emitted = await asyncio.wait_for(next_event, timeout=1)
    await events.aclose()

    assert emitted["event"] == "alert.created"
    assert json.loads(emitted["data"]) == {
        "event": "alert.created",
        "alert": {"alert_id": "a1"},
    }


async def test_monitor_loop_invokes_run_cycle(monkeypatch) -> None:
    calls = 0

    async def fake_run_monitor_from_app(app):  # noqa: ANN001
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(app_module, "run_monitor_from_app", fake_run_monitor_from_app)
    app = SimpleNamespace(state=SimpleNamespace(settings=Settings(_env_file=None)))

    await app_module._monitor_loop(app)

    assert calls == 1


def test_lifespan_respects_monitor_enabled_flag(monkeypatch) -> None:
    async def fake_monitor_loop(app):  # noqa: ANN001
        await asyncio.Event().wait()

    monkeypatch.setattr(app_module, "_monitor_loop", fake_monitor_loop)

    disabled_app = app_with_alerts(settings=Settings(_env_file=None, monitor_enabled=False))
    with TestClient(disabled_app):
        assert disabled_app.state.monitor_task is None

    enabled_app = app_with_alerts(settings=Settings(_env_file=None, monitor_enabled=True))
    with TestClient(enabled_app):
        assert enabled_app.state.monitor_task is not None
