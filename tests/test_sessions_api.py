import json
import time
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.sessions import _session_events
from ecommerce_agent.api.sessions import router as sessions_router
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


def build_test_app() -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_bus = SessionBus()
    app.state.background_tasks = set()

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

        thread = _wait_for_thread(client, session_id, expected_types=["user", "agent_answer"])
        types = [message["type"] for message in thread["messages"]]
        assert types == ["user", "agent_answer"]
        assert thread["messages"][0]["seq"] == 1
        assert thread["messages"][1]["seq"] == 2


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
