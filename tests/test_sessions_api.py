import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.sessions import _session_events
from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.approvals import ApprovalApiError
from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.registry import SessionRegistry, SessionRuntime
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import InMemoryThreadStore


class FakeAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str) -> AsyncIterator[dict]:
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Hi there.")},
        }


class FakeApprovalClient:
    def __init__(
        self,
        *,
        execution_status: str = "consumed",
        approve_changed: bool = True,
        reject_changed: bool = True,
        get_error: ApprovalApiError | None = None,
    ) -> None:
        self.execution_status = execution_status
        self.approve_changed = approve_changed
        self.reject_changed = reject_changed
        self.get_error = get_error
        self.calls: list[str] = []

    async def get_approval(self, approval_id: str) -> dict:
        self.calls.append(f"get:{approval_id}")
        if self.get_error is not None:
            raise self.get_error
        return {
            "approvalId": approval_id,
            "toolName": "purchase_order_create",
            "operationType": "create",
            "status": "pending",
            "operationDetail": '{"title":"Create purchase order"}',
        }

    async def approve(self, approval_id: str) -> dict:
        self.calls.append(f"approve:{approval_id}")
        return {
            "approvalId": approval_id,
            "status": "approved",
            "changed": self.approve_changed,
            "_http_status_code": 200 if self.approve_changed else 409,
        }

    async def execute(self, approval_id: str) -> dict:
        self.calls.append(f"execute:{approval_id}")
        if self.execution_status == "consumed":
            return {
                "approvalId": approval_id,
                "status": "consumed",
                "executionResult": {"purchaseOrderId": 88},
                "message": "approval executed successfully",
            }
        return {
            "approvalId": approval_id,
            "status": self.execution_status,
            "executionResult": {"status": self.execution_status},
            "message": "approved operation is stale; request a fresh approval",
        }

    async def reject(self, approval_id: str, *, reason: str | None = None) -> dict:
        self.calls.append(f"reject:{approval_id}:{reason}")
        return {
            "approvalId": approval_id,
            "status": "rejected",
            "changed": self.reject_changed,
            "rejectionReason": reason,
            "_http_status_code": 200 if self.reject_changed else 409,
        }


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_bus = SessionBus()
    app.state.background_tasks = set()
    app.state.trace_records = {}

    async def build_runtime(session_id: str) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id,
            agent=FakeAgent(),
            mcp_client=object(),
            sandbox=object(),
        )

    app.state.session_registry = SessionRegistry(
        build_runtime=build_runtime,
        idle_ttl_seconds=1800,
        max_live_sessions=50,
    )
    app.state.approval_client_factory = None
    app.include_router(sessions_router)
    return app


def test_create_session_returns_id() -> None:
    with TestClient(build_test_app()) as client:
        response = client.post("/api/sessions")
        assert response.status_code == 201
        assert len(response.json()["session_id"]) == 32


def test_message_runs_turn_and_thread_reload_shows_it() -> None:
    with TestClient(build_test_app()) as client:
        session_id = client.post("/api/sessions").json()["session_id"]

        post = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello"})
        assert post.status_code == 202
        turn_id = post.json()["turn_id"]

        thread = _wait_for_thread(client, session_id, expected_types=["user", "agent_answer"])
        types = [message["type"] for message in thread["messages"]]
        assert types == ["user", "agent_answer"]
        assert thread["messages"][0]["seq"] == 1
        assert thread["messages"][1]["seq"] == 2
        assert _wait_for_trace(client.app, session_id, turn_id).answer == "Hi there."


@pytest.mark.asyncio
async def test_stream_replays_backlog_for_late_subscriber() -> None:
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    store = InMemoryThreadStore()
    bus = SessionBus()
    await store.append(ThreadMessage(session_id="s1", type="user", content="hello"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="Hi there."))

    events = _session_events("s1", FakeRequest(), store, bus)  # type: ignore[arg-type]
    try:
        first = await anext(events)
        second = await anext(events)
    finally:
        await events.aclose()

    assert first["event"] == "thread.append"
    assert json.loads(first["data"])["message"]["type"] == "user"
    assert second["event"] == "thread.append"
    assert json.loads(second["data"])["message"]["type"] == "agent_answer"


def test_message_to_unknown_session_returns_404() -> None:
    with TestClient(build_test_app()) as client:
        response = client.post("/api/sessions/nope/messages", json={"message": "hi"})
        assert response.status_code == 404


def test_approve_endpoint_orchestrates_execute_and_appends_messages() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient()
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")

        assert response.status_code == 200
        body = response.json()
        assert body["approval"]["status"] == "approved"
        assert body["execution"]["status"] == "consumed"
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert approval_client.calls == ["get:a1", "approve:a1", "execute:a1"]
    assert [message["type"] for message in thread["messages"]] == [
        "approval_status",
        "execution_result",
    ]
    assert thread["messages"][0]["status"] == "approved"
    assert thread["messages"][1]["approval_id"] == "a1"
    assert thread["messages"][1]["result"] == {"purchaseOrderId": 88}


def test_approve_endpoint_appends_invalidated_status_without_execution_result() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient(execution_status="invalidated")
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")

        assert response.status_code == 200
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert [message["type"] for message in thread["messages"]] == [
        "approval_status",
        "approval_status",
    ]
    assert thread["messages"][1]["status"] == "invalidated"
    assert "fresh approval" in thread["messages"][1]["reason"]


def test_approve_endpoint_does_not_append_when_approval_is_not_visible() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient(
        get_error=ApprovalApiError(404, {"message": "approval not found"})
    )
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 404
    assert approval_client.calls == ["get:a1"]
    assert thread["messages"] == []


def test_approve_endpoint_replays_execute_without_duplicate_status() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient(approve_changed=False)
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 200
    assert approval_client.calls == ["get:a1", "approve:a1", "execute:a1"]
    assert [message["type"] for message in thread["messages"]] == ["execution_result"]
    assert thread["messages"][0]["approval_id"] == "a1"


def test_reject_endpoint_appends_rejected_status() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient()
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/approvals/a1/reject",
            json={"reason": "too expensive"},
        )
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 200
    assert approval_client.calls == ["get:a1", "reject:a1:too expensive"]
    assert [message["type"] for message in thread["messages"]] == ["approval_status"]
    assert thread["messages"][0]["status"] == "rejected"
    assert thread["messages"][0]["reason"] == "too expensive"


def test_reject_endpoint_does_not_append_when_decision_does_not_change() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient(reject_changed=False)
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(
            f"/api/sessions/{session_id}/approvals/a1/reject",
            json={"reason": "too expensive"},
        )
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 409
    assert approval_client.calls == ["get:a1", "reject:a1:too expensive"]
    assert thread["messages"] == []


def _decode_sse(body: str) -> list[dict]:
    return [
        json.loads(line[len("data:") :].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]


def _wait_for_thread(
    client: TestClient,
    session_id: str,
    *,
    expected_types: list[str],
    timeout_seconds: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_thread: dict | None = None
    while time.monotonic() < deadline:
        last_thread = client.get(f"/api/sessions/{session_id}/thread").json()
        if [message["type"] for message in last_thread["messages"]] == expected_types:
            return last_thread
        time.sleep(0.01)
    assert last_thread is not None
    return last_thread


def _wait_for_trace(app: FastAPI, session_id: str, turn_id: str, timeout_seconds: float = 2.0):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = app.state.trace_records.get(session_id, {}).get(turn_id)
        if record is not None:
            return record
        time.sleep(0.01)
    raise AssertionError(f"missing trace record for {session_id}/{turn_id}")
