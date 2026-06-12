from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.auth.dependencies import current_actor, require
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Action, Actor, Role, User
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings


def _user(role: Role) -> User:
    return User(
        user_id="u1",
        username="alice",
        password_hash="$argon2id$h",
        role=role,
        spring_user_id=7,
        created_at="2026-06-13T00:00:00+00:00",
    )


async def _build(role: Role = Role.OPERATOR) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    await app.state.user_store.create(_user(role))

    @app.get("/me")
    async def me(actor: Annotated[Actor, Depends(current_actor)]):
        return {"user_id": actor.user_id, "role": actor.role}

    @app.get("/audit", dependencies=[Depends(require(Action.AUDIT_SEARCH))])
    async def audit():
        return {"ok": True}

    return app


async def test_me_401_without_cookie():
    app = await _build()
    with TestClient(app) as client:
        assert client.get("/me").status_code == 401


async def test_me_200_with_valid_cookie():
    app = await _build()
    session_id = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", session_id)
        resp = client.get("/me")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "u1"


async def test_require_403_for_viewer():
    app = await _build(Role.VIEWER)
    session_id = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", session_id)
        assert client.get("/audit").status_code == 403


async def test_require_200_for_operator():
    app = await _build(Role.OPERATOR)
    session_id = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", session_id)
        assert client.get("/audit").status_code == 200
