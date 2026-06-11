import asyncio
import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.sessions import _session_events, approve_approval
from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.approvals import ApprovalApiError
from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.registry import SessionRegistry, SessionRuntime
from ecommerce_agent.sessions.store import InMemorySessionStore
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import InMemoryThreadStore
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from ecommerce_agent.trace.store import InMemoryTraceStore


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
        message = (
            "order status transition is not allowed: cancelled -> cancelled"
            if self.execution_status == "invalidated"
            else "approved operation is stale; request a fresh approval"
        )
        return {
            "approvalId": approval_id,
            "status": self.execution_status,
            "executionResult": {"status": self.execution_status},
            "message": message,
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
    app.state.session_store = InMemorySessionStore()
    app.state.session_bus = SessionBus()
    app.state.background_tasks = set()
    app.state.trace_records = {}
    app.state.trace_store = InMemoryTraceStore()
    app.state.approval_clients = {}

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
    app.state.approval_client_factory = lambda session_id: FakeApprovalClient()
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
        assert thread["messages"][0]["turn_id"] == turn_id
        assert thread["messages"][0]["seq"] == 1
        assert thread["messages"][1]["seq"] == 2
        assert _wait_for_trace(client.app, session_id, turn_id).answer == "Hi there."


def test_create_writes_record_and_list_returns_summary() -> None:
    with TestClient(build_test_app()) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello there"})
        _wait_for_thread(client, session_id, expected_types=["user", "agent_answer"])

        listing = client.get("/api/sessions").json()
        assert listing["sessions"][0]["session_id"] == session_id
        assert listing["sessions"][0]["title"] == "hello there"
        assert listing["sessions"][0]["message_count"] == 2
        assert listing["sessions"][0]["last_message_preview"] == "Hi there."

        meta = client.get(f"/api/sessions/{session_id}").json()
        assert meta["session_id"] == session_id
        assert meta["message_count"] == 2


def test_get_unknown_session_404() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost").status_code == 404


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


@pytest.mark.asyncio
async def test_stream_subscribe_first_then_replay_skips_publish_during_replay() -> None:
    class FakeRequest:
        async def is_disconnected(self) -> bool:
            return False

    store = InMemoryThreadStore()
    bus = SessionBus()
    await store.append(ThreadMessage(session_id="s1", type="user", content="hello"))

    class PublishingStore:
        def __init__(self) -> None:
            self.published = False

        async def list_messages(self, session_id: str) -> list[ThreadMessage]:
            if not self.published:
                self.published = True
                stored = await store.append(
                    ThreadMessage(session_id=session_id, type="agent_answer", content="live")
                )
                bus.publish(
                    session_id,
                    {"event": "thread.append", "message": stored.model_dump()},
                )
            return await store.list_messages(session_id)

    events = _session_events("s1", FakeRequest(), PublishingStore(), bus)  # type: ignore[arg-type]
    try:
        first = await anext(events)
        second = await anext(events)
        bus.publish("s1", {"event": "done"})
        third = await asyncio.wait_for(anext(events), timeout=1)
    finally:
        await events.aclose()

    assert json.loads(first["data"])["message"]["seq"] == 1
    assert json.loads(second["data"])["message"]["seq"] == 2
    assert third["event"] == "done"


def test_message_to_unknown_session_returns_404() -> None:
    with TestClient(build_test_app()) as client:
        response = client.post("/api/sessions/nope/messages", json={"message": "hi"})
        assert response.status_code == 404


def test_thread_and_approval_endpoints_404_unknown_session() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/thread").status_code == 404
        assert client.post("/api/sessions/ghost/approvals/a1/approve").status_code == 404
        assert (
            client.post("/api/sessions/ghost/approvals/a1/reject", json={"reason": "x"}).status_code
            == 404
        )


def test_thread_ok_for_created_empty_session() -> None:
    with TestClient(build_test_app()) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.get(f"/api/sessions/{session_id}/thread")
        assert response.status_code == 200
        assert response.json()["messages"] == []


def test_approve_surfaces_java_session_binding_rejection() -> None:
    app = build_test_app()

    class BindingRejectingClient(FakeApprovalClient):
        async def approve(self, approval_id: str) -> dict:
            self.calls.append(f"approve:{approval_id}")
            raise ApprovalApiError(403, {"message": "approval bound to a different session"})

    app.state.approval_client_factory = lambda session_id: BindingRejectingClient()

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 403
    assert thread["messages"] == []


@pytest.mark.asyncio
async def test_second_concurrent_send_409_is_side_effect_free() -> None:
    from fastapi import HTTPException

    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)
    assert await app.state.session_registry.try_begin_turn(session_id) is True

    with pytest.raises(HTTPException) as exc:
        await post_message(
            session_id,
            MessageRequest(message="hi"),
            SimpleNamespace(app=app),  # type: ignore[arg-type]
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {"error": "turn_in_progress"}
    assert await app.state.thread_store.list_messages(session_id) == []


@pytest.mark.asyncio
async def test_message_to_reaped_session_rehydrates() -> None:
    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)
    await app.state.session_registry.close_all()

    result = await post_message(
        session_id,
        MessageRequest(message="hi"),
        SimpleNamespace(app=app),  # type: ignore[arg-type]
    )
    assert "turn_id" in result
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)


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


@pytest.mark.asyncio
async def test_approve_endpoint_publishes_execution_result_to_session_bus() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient()
    app.state.approval_client_factory = lambda session_id: approval_client

    await app.state.session_store.create("s1")
    async with app.state.session_bus.subscription("s1") as sub:
        result = await approve_approval(
            "s1",
            "a1",
            SimpleNamespace(app=app),  # type: ignore[arg-type]
        )
        approval_event = await asyncio.wait_for(sub.queue.get(), timeout=1)
        execution_event = await asyncio.wait_for(sub.queue.get(), timeout=1)

    assert result["execution"]["status"] == "consumed"
    assert approval_event["event"] == "thread.append"
    assert approval_event["message"]["type"] == "approval_status"
    assert execution_event["event"] == "thread.append"
    assert execution_event["message"]["type"] == "execution_result"
    assert execution_event["message"]["approval_id"] == "a1"


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


def test_approve_endpoint_appends_failed_execution_status() -> None:
    app = build_test_app()
    approval_client = FakeApprovalClient(execution_status="failed")
    app.state.approval_client_factory = lambda session_id: approval_client

    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        response = client.post(f"/api/sessions/{session_id}/approvals/a1/approve")
        thread = client.get(f"/api/sessions/{session_id}/thread").json()

    assert response.status_code == 200
    assert response.json()["execution"]["status"] == "failed"
    assert [message["type"] for message in thread["messages"]] == [
        "approval_status",
        "approval_status",
    ]
    assert thread["messages"][1]["status"] == "failed"
    assert thread["messages"][1]["result"] == {"status": "failed"}


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


@pytest.mark.asyncio
async def test_turn_persists_trace_to_store() -> None:
    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)

    result = await post_message(
        session_id, MessageRequest(message="hello"), SimpleNamespace(app=app)
    )
    turn_id = result["turn_id"]
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)

    record = await app.state.trace_store.get(session_id, turn_id)
    assert record is not None and record.turn_id == turn_id


@pytest.mark.asyncio
async def test_trace_save_failure_is_contained() -> None:
    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()

    class FailingTraceStore(InMemoryTraceStore):
        async def save(self, record) -> None:  # noqa: ANN001
            raise RuntimeError("mongo down")

    app.state.trace_store = FailingTraceStore()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)

    result = await post_message(session_id, MessageRequest(message="hi"), SimpleNamespace(app=app))
    turn_id = result["turn_id"]
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)

    types = [m.type for m in await app.state.thread_store.list_messages(session_id)]
    assert types == ["user", "agent_answer"]
    assert app.state.trace_records[session_id][turn_id].turn_id == turn_id


def test_trace_endpoint_returns_timeline() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        turn_id = client.post(
            f"/api/sessions/{session_id}/messages", json={"message": "hello"}
        ).json()["turn_id"]
        _wait_for_trace(app, session_id, turn_id)

        body = client.get(f"/api/sessions/{session_id}/turns/{turn_id}/trace")
        assert body.status_code == 200
        payload = body.json()
        assert payload["turn_id"] == turn_id
        assert payload["session_id"] == session_id
        assert isinstance(payload["spans"], list)


def test_trace_endpoint_falls_back_to_cache() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        app.state.trace_records[session_id] = {
            "t-cache": TraceRecord(session_id=session_id, turn_id="t-cache")
        }

        body = client.get(f"/api/sessions/{session_id}/turns/t-cache/trace")
        assert body.status_code == 200
        assert body.json()["turn_id"] == "t-cache"


def test_trace_endpoint_404s_for_unknown_session_and_turn() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/turns/t1/trace").status_code == 404
        session_id = client.post("/api/sessions").json()["session_id"]
        assert client.get(f"/api/sessions/{session_id}/turns/missing/trace").status_code == 404


def test_trace_endpoint_serves_cache_when_store_get_raises() -> None:
    class RaisingTraceStore(InMemoryTraceStore):
        async def get(self, session_id, turn_id):  # noqa: ANN001
            raise RuntimeError("mongo down")

    app = build_test_app()
    app.state.trace_store = RaisingTraceStore()
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        app.state.trace_records[session_id] = {
            "t1": TraceRecord(session_id=session_id, turn_id="t1")
        }

        body = client.get(f"/api/sessions/{session_id}/turns/t1/trace")
        assert body.status_code == 200
        assert body.json()["turn_id"] == "t1"


def test_trace_export_returns_full_record_as_attachment() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        record = TraceRecord(session_id=session_id, turn_id="t1", answer="hello")
        record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
        app.state.trace_records[session_id] = {"t1": record}

        resp = client.get(f"/api/sessions/{session_id}/turns/t1/trace/export")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == 'attachment; filename="trace-t1.json"'
        body = resp.json()
        assert body["answer"] == "hello"
        assert body["events"][0]["name"] == "order_query"


def test_trace_export_404s_for_unknown_session_and_turn() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/turns/t1/trace/export").status_code == 404
        session_id = client.post("/api/sessions").json()["session_id"]
        assert (
            client.get(f"/api/sessions/{session_id}/turns/missing/trace/export").status_code == 404
        )


@pytest.mark.asyncio
async def test_list_artifacts_projects_from_messages_newest_first() -> None:
    from ecommerce_agent.api.sessions import list_artifacts

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)

    empty = await list_artifacts(session_id, SimpleNamespace(app=app))
    assert empty["artifacts"] == []

    await app.state.thread_store.append(
        ThreadMessage(
            session_id=session_id,
            type="agent_answer",
            content="a",
            turn_id="t1",
            result={
                "artifacts": [
                    {
                        "id": "c0",
                        "kind": "image",
                        "mime_type": "image/svg+xml",
                        "src": "data:image/svg+xml,<svg/>",
                        "tool_name": "generate_line_chart",
                    }
                ]
            },
        )
    )
    await app.state.thread_store.append(
        ThreadMessage(
            session_id=session_id,
            type="agent_answer",
            content="b",
            turn_id="t2",
            result={
                "artifacts": [
                    {
                        "id": "c1",
                        "kind": "image",
                        "mime_type": "image/png",
                        "src": "data:image/png;base64,AAAA",
                        "tool_name": "generate_bar_chart",
                    }
                ]
            },
        )
    )

    body = await list_artifacts(session_id, SimpleNamespace(app=app))
    artifacts = body["artifacts"]
    assert [artifact["id"] for artifact in artifacts] == ["c1", "c0"]
    assert artifacts[0]["turn_id"] == "t2"
    assert artifacts[0]["mime_type"] == "image/png"
    assert artifacts[0]["message_id"]
    assert artifacts[0]["created_at"]
    assert artifacts[1]["tool_name"] == "generate_line_chart"


def test_list_artifacts_404_for_unknown_session() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/artifacts").status_code == 404


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
