from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.audit import router as audit_router
from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.audit.query import AuditQuery, InMemoryAuditStore
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.passwords import hash_password
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


def _messages() -> list[ThreadMessage]:
    return [
        ThreadMessage(
            session_id="s1",
            type="user",
            content="hi",
            actor_id="alice",
            seq=1,
        ),
        ThreadMessage(
            session_id="s2",
            type="execution_result",
            content="done",
            actor_id="bob",
            approval_id="ap-9",
            seq=1,
        ),
    ]


async def test_in_memory_audit_filters() -> None:
    store = InMemoryAuditStore(_messages())

    by_actor = await store.search(AuditQuery(actor_id="alice"))
    by_approval = await store.search(AuditQuery(approval_id="ap-9"))
    by_type = await store.search(AuditQuery(type="execution_result"))

    assert [message.session_id for message in by_actor] == ["s1"]
    assert [message.actor_id for message in by_approval] == ["bob"]
    assert [message.session_id for message in by_type] == ["s2"]


async def _build_app(role: Role) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.audit_store = InMemoryAuditStore(_messages())
    await app.state.user_store.create(
        User(
            user_id="u1",
            username="u",
            password_hash=hash_password("pw"),
            role=role,
            spring_user_id=1,
            created_at="2026-06-13T00:00:00+00:00",
        )
    )
    app.include_router(auth_router)
    app.include_router(audit_router)
    return app


def _login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "u", "password": "pw"})
    assert response.status_code == 200


async def test_audit_endpoint_operator_only() -> None:
    viewer_app = await _build_app(Role.VIEWER)
    with TestClient(viewer_app) as client:
        _login(client)
        assert client.get("/api/audit/messages").status_code == 403

    operator_app = await _build_app(Role.OPERATOR)
    with TestClient(operator_app) as client:
        _login(client)
        response = client.get("/api/audit/messages", params={"actor_id": "bob"})

    assert response.status_code == 200
    assert response.json()["messages"][0]["approval_id"] == "ap-9"
