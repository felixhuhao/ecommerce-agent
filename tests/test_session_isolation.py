from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.passwords import hash_password
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.registry import RuntimeActor, SessionRegistry, SessionRuntime
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
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="hello")},
        }


def _user(user_id: str, username: str, spring_user_id: int) -> User:
    return User(
        user_id=user_id,
        username=username,
        password_hash=hash_password("pw"),
        role=Role.OPERATOR,
        spring_user_id=spring_user_id,
        created_at="2026-06-13T00:00:00+00:00",
    )


async def _build_app() -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = InMemorySessionStore()
    app.state.session_bus = SessionBus()
    app.state.background_tasks = set()
    app.state.trace_records = {}
    app.state.trace_store = InMemoryTraceStore()
    app.state.approval_clients = {}
    app.state.approval_client_factory = lambda session_id: object()

    for user in (_user("alice-id", "alice", 7), _user("bob-id", "bob", 8)):
        await app.state.user_store.create(user)

    async def build_runtime(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id,
            agent=FakeAgent(),
            mcp_client=object(),
            sandbox=object(),
            owner_id=actor.user_id,
            spring_user_id=actor.spring_user_id,
        )

    app.state.session_registry = SessionRegistry(
        build_runtime=build_runtime,
        idle_ttl_seconds=1800,
        max_live_sessions=50,
    )
    app.include_router(auth_router)
    app.include_router(sessions_router)
    return app


def _login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "pw"},
    )
    assert response.status_code == 200


async def test_session_endpoints_require_authentication() -> None:
    app = await _build_app()
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401
        assert client.post("/api/sessions").status_code == 401


async def test_sessions_are_scoped_to_authenticated_actor() -> None:
    app = await _build_app()
    with TestClient(app) as client:
        _login(client, "alice")
        create = client.post("/api/sessions")
        assert create.status_code == 201
        session_id = create.json()["session_id"]

        alice_list = client.get("/api/sessions")
        assert alice_list.status_code == 200
        assert [session["session_id"] for session in alice_list.json()["sessions"]] == [
            session_id
        ]

        _login(client, "bob")
        bob_list = client.get("/api/sessions")
        assert bob_list.status_code == 200
        assert bob_list.json()["sessions"] == []
        assert client.get(f"/api/sessions/{session_id}").status_code == 404
        assert client.get(f"/api/sessions/{session_id}/thread").status_code == 404

        message = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "can I see this?"},
        )
        assert message.status_code == 404
