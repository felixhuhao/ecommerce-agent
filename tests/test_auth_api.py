from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.passwords import hash_password
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings


async def _build(*, auth_cookie_secure: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(_env_file=None, auth_cookie_secure=auth_cookie_secure)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    await app.state.user_store.create(
        User(
            user_id="u1",
            username="alice",
            password_hash=hash_password("pw"),
            role=Role.OPERATOR,
            spring_user_id=7,
            created_at="2026-06-13T00:00:00+00:00",
        )
    )
    app.include_router(auth_router)
    return app


async def test_login_sets_cookie_and_me_returns_actor():
    app = await _build()
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        assert resp.status_code == 200
        assert "ea_session" in resp.cookies
        assert "Secure" not in resp.headers["set-cookie"]
        assert resp.json()["role"] == "operator"
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice"


async def test_login_secure_cookie_flag_is_configurable():
    app = await _build(auth_cookie_secure=True)
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]


async def test_login_bad_password_is_401_generic():
    app = await _build()
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid credentials"


async def test_login_unknown_user_is_401_generic():
    app = await _build()
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "ghost", "password": "pw"})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid credentials"


async def test_logout_revokes_session():
    app = await _build()
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
        assert client.get("/api/auth/me").status_code == 200
        client.post("/api/auth/logout")
        client.cookies.clear()
        assert client.get("/api/auth/me").status_code == 401
