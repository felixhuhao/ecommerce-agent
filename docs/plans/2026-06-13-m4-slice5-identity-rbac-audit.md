# M4 Slice 5 — Identity, Isolation, RBAC & Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authenticated multi-operator identity, per-owner session isolation, role-based permissions (viewer/operator), end-to-end per-actor binding through Spring, a cross-session audit query API, and TTL-based retention.

**Architecture:** A single actor-identity spine. Browser↔FastAPI is authenticated with an HttpOnly server-side session cookie (opaque id → Mongo `auth_sessions` → `users`). FastAPI↔Spring sends the authenticated user's `spring_user_id` as `X-User-Id` on every Spring path (MCP tools + approval REST), where existing `TrustedActorFilter`/`isSameActor` already bind and enforce ownership. RBAC routes through one `can(role, action)` map; viewer runtimes are shaped so they cannot create proposals. Audit search reads the existing thread-message store across sessions; retention bounds it via a Mongo TTL index.

**Tech Stack:** Python 3.12, FastAPI, motor (MongoDB), Pydantic, `argon2-cffi` (password hashing), pytest (`asyncio_mode=auto`), React/TypeScript (frontend shell), Spring Boot (cross-repo test + doc only).

**Spec:** [docs/2026-06-13-m4-slice5-identity-rbac-audit-design.md](../2026-06-13-m4-slice5-identity-rbac-audit-design.md)

**Conventions to follow (from the codebase):**
- Stores are a `Protocol` + an `InMemory*` test double + a `Mongo*` source-of-truth, mirroring `sessions/store.py`.
- API tests build a bare `FastAPI()`, set `app.state.*` to in-memory doubles, `app.include_router(...)`, and drive it with `TestClient` (see `tests/test_sessions_api.py`).
- Run tests: `uv run pytest <path> -v`. Lint: `uv run ruff check <path>`.
- Commit per task (TDD: failing test → run → implement → run → commit).

**Deviation note (approved direction):** the spec says "passlib (argon2)". This plan uses **`argon2-cffi`** directly — passlib is effectively unmaintained and argon2-cffi is the backend it would wrap. The public API (`hash_password`/`verify_password`) is identical, so the spec intent (argon2, constant-time verify) holds.

---

## File Structure

**New (Python)**
- `src/ecommerce_agent/auth/__init__.py` — package marker.
- `src/ecommerce_agent/auth/models.py` — `Role`, `Action`, `User`, `Actor`.
- `src/ecommerce_agent/auth/passwords.py` — `hash_password`, `verify_password`.
- `src/ecommerce_agent/auth/permissions.py` — `can(role, action)`.
- `src/ecommerce_agent/auth/users_store.py` — `UserStore` protocol, `InMemoryUserStore`, `MongoUserStore`.
- `src/ecommerce_agent/auth/login_sessions.py` — `LoginSessionStore` protocol, `InMemoryLoginSessionStore`, `MongoLoginSessionStore`.
- `src/ecommerce_agent/auth/dependencies.py` — `current_actor`, `require(action)`.
- `src/ecommerce_agent/api/auth.py` — login/logout/me router.
- `src/ecommerce_agent/audit/__init__.py` — package marker.
- `src/ecommerce_agent/audit/query.py` — `AuditQuery`, `AuditStore` protocol, `InMemoryAuditStore`.
- `src/ecommerce_agent/audit/mongo.py` — `MongoAuditStore`.
- `src/ecommerce_agent/api/audit.py` — audit query router.

**Modified (Python)**
- `src/ecommerce_agent/config.py` — auth/audit settings.
- `src/ecommerce_agent/sessions/store.py` — `owner_id`.
- `src/ecommerce_agent/sessions/registry.py` — `RuntimeActor`, actor-bound create/rebuild, cached-owner check, `SessionRuntime.owner_id/spring_user_id`.
- `src/ecommerce_agent/sessions/factory.py` — actor `spring_user_id` into MCP client; role-shaped specialist map; `RoutedSessionAgent` policy-deny.
- `src/ecommerce_agent/api/sessions.py` — `current_actor` deps, ownership 403/404, actor wiring.
- `src/ecommerce_agent/api/app.py` — wire stores + routers + cookie config.
- `src/ecommerce_agent/threads/mongo.py` — `expire_at` + TTL index + audit indexes.
- `src/ecommerce_agent/trace/mongo.py` — `expire_at` + TTL index.
- `src/ecommerce_agent/cli.py` — `users add` seed command.
- `pyproject.toml` — add `argon2-cffi`.

**New tests**
- `tests/test_passwords.py`, `tests/test_permissions.py`, `tests/test_auth_models.py`, `tests/test_auth_stores.py`, `tests/test_auth_dependencies.py`, `tests/test_auth_api.py`, `tests/test_session_isolation.py`, `tests/test_runtime_actor.py`, `tests/test_role_shaped_runtime.py`, `tests/test_audit.py`, `tests/test_retention.py`.

**Modified tests:** `tests/test_session_factory.py`, `tests/test_session_registry.py`, `tests/test_sessions_api.py`, `tests/test_cli.py`, `tests/test_config.py`.

**Cross-repo (Java)**
- `ecommerce-mcp-server/src/test/java/com/ecommerce/agent/controller/ApprovalControllerTest.java` — cross-actor denial test.
- `ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md` — trust-boundary contract.

---

## Task 1: Add `argon2-cffi` dependency + password hashing

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Create: `src/ecommerce_agent/auth/__init__.py`
- Create: `src/ecommerce_agent/auth/passwords.py`
- Test: `tests/test_passwords.py`

- [ ] **Step 1: Add the dependency and sync**

In `pyproject.toml`, add `"argon2-cffi>=23.1.0",` to the `[project] dependencies` list (keep alphabetical-ish order; place after `"langchain-openai..."`).

Run: `uv sync`
Expected: resolves and installs `argon2-cffi`.

- [ ] **Step 2: Create the package marker**

Create `src/ecommerce_agent/auth/__init__.py` (empty file).

- [ ] **Step 3: Write the failing test**

```python
# tests/test_passwords.py
from ecommerce_agent.auth.passwords import hash_password, verify_password


def test_hash_is_not_plaintext_and_verifies():
    h = hash_password("s3cret-pw")
    assert h != "s3cret-pw"
    assert h.startswith("$argon2")
    assert verify_password("s3cret-pw", h) is True


def test_verify_rejects_wrong_password():
    h = hash_password("s3cret-pw")
    assert verify_password("wrong", h) is False


def test_verify_rejects_malformed_hash():
    assert verify_password("anything", "not-a-hash") is False


def test_hashes_are_salted_and_differ():
    assert hash_password("same") != hash_password("same")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_passwords.py -v`
Expected: FAIL with `ModuleNotFoundError: ecommerce_agent.auth.passwords`.

- [ ] **Step 5: Implement**

```python
# src/ecommerce_agent/auth/passwords.py
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Return an argon2 hash of `password` (salt embedded)."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time verify; False on mismatch or malformed hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, ValueError):
        return False
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_passwords.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/ecommerce_agent/auth/__init__.py src/ecommerce_agent/auth/passwords.py tests/test_passwords.py
git commit -m "feat(auth): add argon2 password hashing"
```

---

## Task 2: Roles, actions, and the `can()` permission map

**Files:**
- Create: `src/ecommerce_agent/auth/models.py`
- Create: `src/ecommerce_agent/auth/permissions.py`
- Test: `tests/test_auth_models.py`, `tests/test_permissions.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_models.py
from ecommerce_agent.auth.models import Actor, Role, User


def test_role_values():
    assert Role.VIEWER == "viewer"
    assert Role.OPERATOR == "operator"


def test_user_and_actor_roundtrip():
    user = User(
        user_id="u1",
        username="alice",
        password_hash="$argon2id$...",
        role=Role.OPERATOR,
        spring_user_id=7,
        created_at="2026-06-13T00:00:00+00:00",
    )
    actor = Actor.from_user(user)
    assert actor.user_id == "u1"
    assert actor.username == "alice"
    assert actor.role == Role.OPERATOR
    assert actor.spring_user_id == 7
    assert not hasattr(actor, "password_hash")
```

```python
# tests/test_permissions.py
from ecommerce_agent.auth.models import Action, Role
from ecommerce_agent.auth.permissions import can


def test_operator_can_everything_gated():
    assert can(Role.OPERATOR, Action.PROPOSE)
    assert can(Role.OPERATOR, Action.APPROVE)
    assert can(Role.OPERATOR, Action.AUDIT_SEARCH)


def test_viewer_can_nothing_gated():
    assert not can(Role.VIEWER, Action.PROPOSE)
    assert not can(Role.VIEWER, Action.APPROVE)
    assert not can(Role.VIEWER, Action.AUDIT_SEARCH)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_models.py tests/test_permissions.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement models**

```python
# src/ecommerce_agent/auth/models.py
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"


class Action(StrEnum):
    PROPOSE = "propose"
    APPROVE = "approve"
    AUDIT_SEARCH = "audit_search"


class User(BaseModel):
    user_id: str
    username: str
    password_hash: str
    role: Role
    spring_user_id: int
    created_at: str


class Actor(BaseModel):
    """Resolved request principal. Carries no secret."""

    user_id: str
    username: str
    role: Role
    spring_user_id: int

    @classmethod
    def from_user(cls, user: User) -> "Actor":
        return cls(
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            spring_user_id=user.spring_user_id,
        )
```

- [ ] **Step 4: Implement permissions**

```python
# src/ecommerce_agent/auth/permissions.py
from __future__ import annotations

from ecommerce_agent.auth.models import Action, Role

# Single source of truth for authorization. Add a role => add one entry.
_PERMISSIONS: dict[Role, frozenset[Action]] = {
    Role.VIEWER: frozenset(),
    Role.OPERATOR: frozenset({Action.PROPOSE, Action.APPROVE, Action.AUDIT_SEARCH}),
}


def can(role: Role, action: Action) -> bool:
    return action in _PERMISSIONS.get(role, frozenset())
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_auth_models.py tests/test_permissions.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/auth/models.py src/ecommerce_agent/auth/permissions.py tests/test_auth_models.py tests/test_permissions.py
git commit -m "feat(auth): roles, actions, and central can() permission map"
```

---

## Task 3: User store and login-session store (in-memory doubles + Mongo impls)

**Files:**
- Create: `src/ecommerce_agent/auth/users_store.py`
- Create: `src/ecommerce_agent/auth/login_sessions.py`
- Test: `tests/test_auth_stores.py`

- [ ] **Step 1: Write the failing tests (in-memory behaviors)**

```python
# tests/test_auth_stores.py
from datetime import UTC, datetime, timedelta

import pytest

from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.users_store import InMemoryUserStore


def _user(username: str = "alice") -> User:
    return User(
        user_id=f"id-{username}",
        username=username,
        password_hash="$argon2id$h",
        role=Role.OPERATOR,
        spring_user_id=7,
        created_at="2026-06-13T00:00:00+00:00",
    )


async def test_user_store_create_and_lookup():
    store = InMemoryUserStore()
    await store.create(_user("alice"))
    fetched = await store.get_by_username("alice")
    assert fetched is not None and fetched.user_id == "id-alice"
    assert (await store.get_by_id("id-alice")).username == "alice"
    assert await store.get_by_username("missing") is None


async def test_user_store_rejects_duplicate_username():
    store = InMemoryUserStore()
    await store.create(_user("alice"))
    with pytest.raises(ValueError):
        await store.create(_user("alice"))


async def test_login_session_create_get_delete():
    store = InMemoryLoginSessionStore()
    sid = await store.create("id-alice", ttl_seconds=3600)
    rec = await store.get(sid)
    assert rec is not None and rec["user_id"] == "id-alice"
    await store.delete(sid)
    assert await store.get(sid) is None


async def test_login_session_expired_returns_none():
    store = InMemoryLoginSessionStore(now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    sid = await store.create("id-alice", ttl_seconds=10)
    # advance the clock past expiry
    store.now = lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=11)
    assert await store.get(sid) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_stores.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the user store**

```python
# src/ecommerce_agent/auth/users_store.py
from __future__ import annotations

import asyncio
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from ecommerce_agent.auth.models import User
from ecommerce_agent.config import Settings


class UserStore(Protocol):
    async def create(self, user: User) -> None: ...
    async def get_by_username(self, username: str) -> User | None: ...
    async def get_by_id(self, user_id: str) -> User | None: ...


class InMemoryUserStore:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._by_username: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, user: User) -> None:
        async with self._lock:
            if user.username in self._by_username:
                raise ValueError(f"username already exists: {user.username}")
            self._by_id[user.user_id] = user
            self._by_username[user.username] = user.user_id

    async def get_by_username(self, username: str) -> User | None:
        user_id = self._by_username.get(username)
        return self._by_id.get(user_id) if user_id else None

    async def get_by_id(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)


class MongoUserStore:
    def __init__(self, *, users: Any, client: Any | None = None) -> None:
        self._users = users
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoUserStore":
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(users=db["users"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._users.create_index("username", unique=True)

    async def create(self, user: User) -> None:
        try:
            await self._users.insert_one({"_id": user.user_id, **user.model_dump()})
        except DuplicateKeyError as exc:
            raise ValueError(f"username already exists: {user.username}") from exc

    async def get_by_username(self, username: str) -> User | None:
        doc = await self._users.find_one({"username": username})
        return self._to_user(doc) if doc else None

    async def get_by_id(self, user_id: str) -> User | None:
        doc = await self._users.find_one({"_id": user_id})
        return self._to_user(doc) if doc else None

    @staticmethod
    def _to_user(doc: dict[str, Any]) -> User:
        return User(**{key: value for key, value in doc.items() if key != "_id"})
```

- [ ] **Step 4: Implement the login-session store**

```python
# src/ecommerce_agent/auth/login_sessions.py
from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoginSessionStore(Protocol):
    async def create(self, user_id: str, *, ttl_seconds: int) -> str: ...
    async def get(self, session_id: str) -> dict[str, Any] | None: ...
    async def delete(self, session_id: str) -> None: ...


class InMemoryLoginSessionStore:
    def __init__(self, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.now = now
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, user_id: str, *, ttl_seconds: int) -> str:
        session_id = secrets.token_urlsafe(32)
        now = self.now()
        async with self._lock:
            self._records[session_id] = {
                "user_id": user_id,
                "created_at": now,
                "expire_at": now + timedelta(seconds=ttl_seconds),
            }
        return session_id

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return None
            if record["expire_at"] <= self.now():
                del self._records[session_id]
                return None
            return dict(record)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)


class MongoLoginSessionStore:
    def __init__(self, *, sessions: Any, client: Any | None = None) -> None:
        self._sessions = sessions
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoLoginSessionStore":
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(sessions=db["auth_sessions"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        # Mongo reaps expired login sessions automatically.
        await self._sessions.create_index("expire_at", expireAfterSeconds=0)

    async def create(self, user_id: str, *, ttl_seconds: int) -> str:
        session_id = secrets.token_urlsafe(32)
        now = _utcnow()
        await self._sessions.insert_one(
            {
                "_id": session_id,
                "user_id": user_id,
                "created_at": now,
                "expire_at": now + timedelta(seconds=ttl_seconds),
            }
        )
        return session_id

    async def get(self, session_id: str) -> dict[str, Any] | None:
        doc = await self._sessions.find_one({"_id": session_id})
        if doc is None:
            return None
        expire_at = doc["expire_at"]
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        if expire_at <= _utcnow():
            await self.delete(session_id)
            return None
        return {"user_id": doc["user_id"], "created_at": doc["created_at"], "expire_at": expire_at}

    async def delete(self, session_id: str) -> None:
        await self._sessions.delete_one({"_id": session_id})
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_auth_stores.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/auth/users_store.py src/ecommerce_agent/auth/login_sessions.py tests/test_auth_stores.py
git commit -m "feat(auth): user store and login-session store"
```

---

## Task 4: Auth config settings

**Files:**
- Modify: `src/ecommerce_agent/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py  (add to the existing file)
def test_auth_and_audit_defaults():
    from ecommerce_agent.config import Settings

    s = Settings(_env_file=None)
    assert s.auth_cookie_name == "ea_session"
    assert s.auth_cookie_secure is False
    assert s.auth_session_ttl_seconds == 28800
    assert s.audit_retention_days == 90
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py::test_auth_and_audit_defaults -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement**

In `src/ecommerce_agent/config.py`, add to `Settings` (after the `frontend_dist_dir` line):

```python
    # M4 slice 5: auth / audit
    auth_cookie_name: str = "ea_session"
    auth_cookie_secure: bool = False
    auth_session_ttl_seconds: int = Field(default=28800, gt=0)
    audit_retention_days: int = Field(default=90, gt=0)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/config.py tests/test_config.py
git commit -m "feat(config): auth cookie and audit retention settings"
```

---

## Task 5: Auth dependencies (`current_actor`, `require`)

**Files:**
- Create: `src/ecommerce_agent/auth/dependencies.py`
- Test: `tests/test_auth_dependencies.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_dependencies.py
import pytest
from fastapi import FastAPI, Request
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


async def _build(role: Role = Role.OPERATOR):
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    await app.state.user_store.create(_user(role))

    @app.get("/me")
    async def me(actor: Actor = __import__("fastapi").Depends(current_actor)):
        return {"user_id": actor.user_id, "role": actor.role}

    @app.get("/audit", dependencies=[__import__("fastapi").Depends(require(Action.AUDIT_SEARCH))])
    async def audit():
        return {"ok": True}

    return app


async def test_me_401_without_cookie():
    app = await _build()
    with TestClient(app) as client:
        assert client.get("/me").status_code == 401


async def test_me_200_with_valid_cookie():
    app = await _build()
    sid = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", sid)
        resp = client.get("/me")
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "u1"


async def test_require_403_for_viewer():
    app = await _build(Role.VIEWER)
    sid = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", sid)
        assert client.get("/audit").status_code == 403


async def test_require_200_for_operator():
    app = await _build(Role.OPERATOR)
    sid = await app.state.login_session_store.create("u1", ttl_seconds=3600)
    with TestClient(app) as client:
        client.cookies.set("ea_session", sid)
        assert client.get("/audit").status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_dependencies.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/ecommerce_agent/auth/dependencies.py
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status

from ecommerce_agent.auth.models import Action, Actor
from ecommerce_agent.auth.permissions import can


async def current_actor(request: Request) -> Actor:
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.auth_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    record = await request.app.state.login_session_store.get(cookie)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    user = await request.app.state.user_store.get_by_id(record["user_id"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return Actor.from_user(user)


def require(action: Action) -> Callable[[Actor], Awaitable[Actor]]:
    async def dependency(actor: Actor = Depends(current_actor)) -> Actor:
        if not can(actor.role, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return actor

    return dependency
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_auth_dependencies.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/auth/dependencies.py tests/test_auth_dependencies.py
git commit -m "feat(auth): current_actor and require() FastAPI dependencies"
```

---

## Task 6: Auth API router (login/logout/me) + app wiring

**Files:**
- Create: `src/ecommerce_agent/api/auth.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_auth_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_api.py
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.passwords import hash_password
from ecommerce_agent.auth.users_store import InMemoryUserStore
from ecommerce_agent.config import Settings


async def _build():
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
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
        assert resp.json()["role"] == "operator"
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "alice"


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
        client.cookies.clear()  # browser would drop the cleared cookie
        assert client.get("/api/auth/me").status_code == 401
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_api.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the router**

```python
# src/ecommerce_agent/api/auth.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, StringConstraints

from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.models import Actor
from ecommerce_agent.auth.passwords import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    password: Annotated[str, StringConstraints(min_length=1)]


def _actor_public(actor: Actor) -> dict:
    return {
        "user_id": actor.user_id,
        "username": actor.username,
        "role": actor.role,
        "spring_user_id": actor.spring_user_id,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    user = await request.app.state.user_store.get_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    session_id = await request.app.state.login_session_store.create(
        user.user_id, ttl_seconds=settings.auth_session_ttl_seconds
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=session_id,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return _actor_public(Actor.from_user(user))


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.auth_cookie_name)
    if cookie:
        await request.app.state.login_session_store.delete(cookie)
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
async def me(actor: Actor = Depends(current_actor)) -> dict:
    return _actor_public(actor)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_auth_api.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Wire stores + router into the app**

In `src/ecommerce_agent/api/app.py`:

Add imports near the other store imports:
```python
from ecommerce_agent.api.auth import router as auth_router
from ecommerce_agent.auth.login_sessions import MongoLoginSessionStore
from ecommerce_agent.auth.users_store import MongoUserStore
```

In `lifespan`, after the `trace_store` wiring block, add:
```python
    app.state.user_store = getattr(app.state, "user_store", None) or MongoUserStore.from_settings(
        settings
    )
    app.state.login_session_store = getattr(
        app.state, "login_session_store", None
    ) or MongoLoginSessionStore.from_settings(settings)
    for store in (app.state.user_store, app.state.login_session_store):
        ensure = getattr(store, "ensure_indexes", None)
        if callable(ensure):
            await ensure()
```

In the `lifespan` `finally` block, after `trace_store_close`, add close calls:
```python
        for store in (
            getattr(app.state, "user_store", None),
            getattr(app.state, "login_session_store", None),
        ):
            close = getattr(store, "close", None)
            if callable(close):
                close()
```

In `create_app`, after `app.state.approval_clients = None`, add:
```python
    app.state.user_store = None
    app.state.login_session_store = None
```

And register the router (before `app.include_router(sessions_router)`):
```python
    app.include_router(auth_router)
```

- [ ] **Step 6: Run the app test suite to verify no regression**

Run: `uv run pytest tests/test_app.py tests/test_auth_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/api/auth.py src/ecommerce_agent/api/app.py tests/test_auth_api.py
git commit -m "feat(auth): login/logout/me API and app wiring"
```

---

## Task 7: Session ownership + isolation enforcement

**Files:**
- Modify: `src/ecommerce_agent/sessions/store.py`
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_session_store.py` (extend), `tests/test_session_isolation.py` (new)

- [ ] **Step 1: Write the failing store tests**

```python
# tests/test_session_store.py  (add)
from ecommerce_agent.sessions.store import InMemorySessionStore


async def test_create_stamps_owner_and_list_filters_by_owner():
    store = InMemorySessionStore()
    await store.create("s1", owner_id="alice")
    await store.create("s2", owner_id="bob")
    assert (await store.get("s1"))["owner_id"] == "alice"
    alice = [r["session_id"] for r in await store.list_records(owner_id="alice")]
    assert alice == ["s1"]
    everyone = [r["session_id"] for r in await store.list_records()]
    assert set(everyone) == {"s1", "s2"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_session_store.py::test_create_stamps_owner_and_list_filters_by_owner -v`
Expected: FAIL (`create()` takes no `owner_id`).

- [ ] **Step 3: Implement `owner_id` in the session store**

In `src/ecommerce_agent/sessions/store.py`:

Update the `SessionStore` Protocol `create` and `list_records` signatures:
```python
    async def create(self, session_id: str, *, owner_id: str) -> None: ...
    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]: ...
```

`InMemorySessionStore.create`:
```python
    async def create(self, session_id: str, *, owner_id: str) -> None:
        async with self._lock:
            if session_id in self._records:
                return
            self._records[session_id] = {
                "session_id": session_id,
                "owner_id": owner_id,
                "title": None,
                "created_at": _now_iso(),
            }
            self._seq[session_id] = next(self._order)
```

`InMemorySessionStore.list_records`:
```python
    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            records = [
                dict(record)
                for _, record in sorted(
                    self._records.items(),
                    key=lambda item: self._seq[item[0]],
                    reverse=True,
                )
            ]
        if owner_id is not None:
            records = [r for r in records if r.get("owner_id") == owner_id]
        return records
```

`MongoSessionStore.create`:
```python
    async def create(self, session_id: str, *, owner_id: str) -> None:
        await self._sessions.update_one(
            {"_id": session_id},
            {"$setOnInsert": {"owner_id": owner_id, "title": None, "created_at": _now_iso()}},
            upsert=True,
        )
```

`MongoSessionStore.list_records`:
```python
    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        query = {"owner_id": owner_id} if owner_id is not None else {}
        cursor = self._sessions.find(query).sort("created_at", -1)
        return [self._to_record(doc) async for doc in cursor]
```

`MongoSessionStore._to_record` — add `owner_id`:
```python
    @staticmethod
    def _to_record(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": doc["_id"],
            "owner_id": doc.get("owner_id"),
            "title": doc.get("title"),
            "created_at": doc.get("created_at"),
        }
```

- [ ] **Step 4: Run to verify the store test passes**

Run: `uv run pytest tests/test_session_store.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing isolation tests**

```python
# tests/test_session_isolation.py
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


class _FakeAgent:
    async def astream_events(self, inputs, config, version):
        if False:
            yield {}


async def _build():
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.session_store = InMemorySessionStore()
    app.state.thread_store = InMemoryThreadStore()
    app.state.trace_store = InMemoryTraceStore()
    app.state.trace_records = {}
    app.state.session_bus = SessionBus()
    app.state.background_tasks = set()
    app.state.approval_clients = {}

    async def _build_runtime(session_id: str, actor: RuntimeActor) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id,
            agent=_FakeAgent(),
            mcp_client=None,
            sandbox=None,
            owner_id=actor.user_id,
            spring_user_id=actor.spring_user_id,
        )

    app.state.session_registry = SessionRegistry(
        build_runtime=_build_runtime, idle_ttl_seconds=1800, max_live_sessions=50
    )
    for username, role in (("alice", Role.OPERATOR), ("bob", Role.OPERATOR)):
        await app.state.user_store.create(
            User(
                user_id=username,
                username=username,
                password_hash=hash_password("pw"),
                role=role,
                spring_user_id=1 if username == "alice" else 2,
                created_at="2026-06-13T00:00:00+00:00",
            )
        )
    app.include_router(auth_router)
    app.include_router(sessions_router)
    return app


def _login(client, username):
    client.post("/api/auth/login", json={"username": username, "password": "pw"})


async def test_sessions_require_authentication():
    app = await _build()
    with TestClient(app) as client:
        assert client.get("/api/sessions").status_code == 401
        assert client.post("/api/sessions").status_code == 401


async def test_user_cannot_see_or_access_other_users_session():
    app = await _build()
    with TestClient(app) as client:
        _login(client, "alice")
        session_id = client.post("/api/sessions").json()["session_id"]
        # Alice sees it in her list.
        assert any(s["session_id"] == session_id for s in client.get("/api/sessions").json()["sessions"])

        client.cookies.clear()
        _login(client, "bob")
        # Bob's list is empty and direct access 404s.
        assert client.get("/api/sessions").json()["sessions"] == []
        assert client.get(f"/api/sessions/{session_id}").status_code == 404
        assert client.get(f"/api/sessions/{session_id}/thread").status_code == 404
        assert client.post(
            f"/api/sessions/{session_id}/messages", json={"message": "hi"}
        ).status_code == 404
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_session_isolation.py -v`
Expected: FAIL (endpoints not yet auth/owner gated; `SessionRuntime`/`RuntimeActor`/registry signature not yet updated — these land in Task 8, so this test stays red until then). Note: this is the one cross-task red test; proceed to Task 8 to green it.

- [ ] **Step 7: Add ownership enforcement to the session endpoints**

In `src/ecommerce_agent/api/sessions.py`:

Add imports:
```python
from fastapi import Depends
from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.models import Action, Actor
from ecommerce_agent.auth.permissions import can
from ecommerce_agent.sessions.registry import RuntimeActor
```

Replace `_require_session` with an owner-aware helper:
```python
async def _require_owned_session(request: Request, session_id: str, actor: Actor) -> dict[str, Any]:
    record = await request.app.state.session_store.get(session_id)
    if record is None or record.get("owner_id") != actor.user_id:
        raise HTTPException(status_code=404, detail="session not found")
    return record
```

Update each route to depend on `current_actor` and use the owner check. Concretely:

`create_session`:
```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, actor: Actor = Depends(current_actor)) -> dict[str, str]:
    runtime_actor = RuntimeActor(
        user_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
        can_propose=can(actor.role, Action.PROPOSE),
    )
    session_id = await request.app.state.session_registry.create(runtime_actor)
    await request.app.state.session_store.create(session_id, owner_id=actor.user_id)
    return {"session_id": session_id}
```

`list_sessions`:
```python
@router.get("")
async def list_sessions(request: Request, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
    store = request.app.state.session_store
    thread_store = request.app.state.thread_store
    summaries = []
    for record in await store.list_records(owner_id=actor.user_id):
        session_id = record["session_id"]
        latest = await thread_store.latest_message(session_id)
        summaries.append(
            {
                **record,
                "last_message_preview": latest.content[:120] if latest else None,
                "message_count": await thread_store.count_messages(session_id),
            }
        )
    return {"sessions": summaries}
```

`get_session`, `get_thread`, `list_artifacts`, `get_trace`, `export_trace`, `stream`: add `actor: Actor = Depends(current_actor)` and replace `await _require_session(request, session_id)` (or the inline 404 in `get_session`) with `await _require_owned_session(request, session_id, actor)`. For `get_session`, use the returned record:
```python
@router.get("/{session_id}")
async def get_session(session_id: str, request: Request, actor: Actor = Depends(current_actor)) -> dict[str, Any]:
    record = await _require_owned_session(request, session_id, actor)
    return {**record, "message_count": await request.app.state.thread_store.count_messages(session_id)}
```

`post_message` — add the dep, owner check, pass the actor to the runtime, and stamp the real `actor_id`:
```python
@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
    actor: Actor = Depends(current_actor),
) -> dict[str, Any]:
    registry = request.app.state.session_registry
    session_store = request.app.state.session_store
    await _require_owned_session(request, session_id, actor)

    if not await registry.try_begin_turn(session_id):
        raise HTTPException(status_code=409, detail={"error": "turn_in_progress"})

    runtime_actor = RuntimeActor(
        user_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
        can_propose=can(actor.role, Action.PROPOSE),
    )

    async def _known(sid: str) -> bool:
        record = await session_store.get(sid)
        return record is not None and record.get("owner_id") == actor.user_id

    try:
        runtime = await registry.get_or_create_runtime(session_id, runtime_actor, _known)
    except KeyError as exc:
        await registry.end_turn(session_id)
        raise HTTPException(status_code=404, detail="session not found") from exc
    except Exception:
        await registry.end_turn(session_id)
        raise
```
Then in the same function, change the user `ThreadMessage(... actor_id="operator" ...)` to `actor_id=actor.user_id`, and the `approval_client = _approval_client(request, session_id)` line to `approval_client = _approval_client(request, session_id, actor)` (see Task 9 for the helper signature change). Keep the rest of the function body unchanged.

- [ ] **Step 8: Commit (test still red until Task 8 lands the registry signature)**

```bash
git add src/ecommerce_agent/sessions/store.py src/ecommerce_agent/api/sessions.py tests/test_session_store.py tests/test_session_isolation.py
git commit -m "feat(sessions): owner_id + auth/ownership gating on session endpoints"
```

---

## Task 8: Actor-bound session runtime (registry + factory)

**Files:**
- Modify: `src/ecommerce_agent/sessions/registry.py`
- Modify: `src/ecommerce_agent/sessions/factory.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_runtime_actor.py` (new), `tests/test_session_registry.py` (update), `tests/test_session_factory.py` (update)

- [ ] **Step 1: Write the failing registry tests**

```python
# tests/test_runtime_actor.py
import pytest

from ecommerce_agent.sessions.registry import RuntimeActor, SessionRegistry, SessionRuntime


def _runtime(session_id: str, actor: RuntimeActor) -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        agent=object(),
        mcp_client=None,
        sandbox=None,
        owner_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
    )


async def test_create_binds_owner_and_spring_id():
    async def build(session_id, actor):
        return _runtime(session_id, actor)

    reg = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    actor = RuntimeActor(user_id="alice", spring_user_id=7, can_propose=True)
    session_id = await reg.create(actor)
    runtime = await reg.get(session_id)
    assert runtime.owner_id == "alice"
    assert runtime.spring_user_id == 7


async def test_cached_runtime_owner_mismatch_raises():
    async def build(session_id, actor):
        return _runtime(session_id, actor)

    reg = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)
    alice = RuntimeActor(user_id="alice", spring_user_id=7, can_propose=True)
    session_id = await reg.create(alice)

    async def known(_sid):
        return True

    bob = RuntimeActor(user_id="bob", spring_user_id=8, can_propose=True)
    with pytest.raises(PermissionError):
        await reg.get_or_create_runtime(session_id, bob, known)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_runtime_actor.py -v`
Expected: FAIL (`RuntimeActor` missing; `create()`/`get_or_create_runtime()` signatures differ; `SessionRuntime` has no `owner_id`).

- [ ] **Step 3: Implement registry changes**

In `src/ecommerce_agent/sessions/registry.py`:

Add the DTO and extend `SessionRuntime`:
```python
@dataclass(frozen=True)
class RuntimeActor:
    user_id: str
    spring_user_id: int
    can_propose: bool


@dataclass
class SessionRuntime:
    session_id: str
    agent: Any
    mcp_client: Any
    sandbox: Any
    owner_id: str | None = None
    spring_user_id: int | None = None
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    # ... existing touch()/idle_seconds()/close() unchanged
```

Update the builder type and methods:
```python
BuildRuntime = Callable[[str, RuntimeActor], Awaitable[SessionRuntime]]


    async def create(self, actor: RuntimeActor) -> str:
        session_id = uuid.uuid4().hex
        evicted: list[SessionRuntime] = []
        try:
            runtime = await self._build_runtime(session_id, actor)
        except Exception:
            await self._close_evicted(evicted)
            raise
        async with self._lock:
            try:
                evicted.extend(self._make_room_locked())
                self._runtimes[session_id] = runtime
            except Exception:
                evicted.append(runtime)
                raise
        await self._close_evicted(evicted)
        return session_id

    async def get_or_create_runtime(
        self,
        session_id: str,
        actor: RuntimeActor,
        session_known: Callable[[str], Awaitable[bool]],
    ) -> SessionRuntime:
        async with self._lock:
            cached = self._runtimes.get(session_id)
            if cached is not None:
                if cached.owner_id is not None and cached.owner_id != actor.user_id:
                    raise PermissionError("runtime owner mismatch")
                cached.touch()
                return cached

        if not await session_known(session_id):
            raise KeyError(session_id)

        runtime = await self._build_runtime(session_id, actor)
        evicted: list[SessionRuntime] = []
        loser: SessionRuntime | None = None
        async with self._lock:
            winner = self._runtimes.get(session_id)
            if winner is not None:
                if winner.owner_id is not None and winner.owner_id != actor.user_id:
                    raise PermissionError("runtime owner mismatch")
                loser = runtime
                winner.touch()
            else:
                evicted.extend(self._make_room_locked())
                self._runtimes[session_id] = runtime
        await self._close_evicted([loser] if loser else evicted)
        return winner if loser is not None else runtime
```

- [ ] **Step 4: Run to verify registry tests pass**

Run: `uv run pytest tests/test_runtime_actor.py -v`
Expected: PASS. Then update existing `tests/test_session_registry.py` call sites to the new signatures (pass a `RuntimeActor` to `create`/`get_or_create_runtime`, and a 2-arg `build_runtime(session_id, actor)`); run `uv run pytest tests/test_session_registry.py -v` until green.

- [ ] **Step 5: Update the factory to bind the actor's spring id**

In `src/ecommerce_agent/sessions/factory.py`, change the signature and the MCP client construction:
```python
from ecommerce_agent.sessions.registry import RuntimeActor, SessionRuntime


async def build_session_runtime(
    session_id: str, settings: Settings, actor: RuntimeActor
) -> SessionRuntime:
    """Build a per-session runtime bound to `actor` (spring id + propose capability)."""
    mcp_client = build_mcp_client(
        settings,
        user_id=str(actor.spring_user_id),
        session_id=session_id,
    )
    # ... existing tool fetch / sandbox / model unchanged ...
```
And at the return, populate the new fields:
```python
    return SessionRuntime(
        session_id=session_id,
        agent=routed_agent,
        mcp_client=mcp_client,
        sandbox=sandbox,
        owner_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
    )
```
(The `agents`/role-shaping change lands in Task 10; for now keep the existing `analyst` + `order_manager` map.)

- [ ] **Step 6: Update the app runtime builder**

In `src/ecommerce_agent/api/app.py`, change `make_runtime_builder`:
```python
def make_runtime_builder(settings: Settings):
    async def build_runtime(session_id: str, actor):
        return await build_session_runtime(session_id, settings, actor)

    return build_runtime
```

- [ ] **Step 7: Run factory + isolation tests**

Update `tests/test_session_factory.py` call sites to pass a `RuntimeActor`. Then:
Run: `uv run pytest tests/test_session_factory.py tests/test_runtime_actor.py tests/test_session_isolation.py -v`
Expected: PASS (isolation test from Task 7 now goes green).

- [ ] **Step 8: Commit**

```bash
git add src/ecommerce_agent/sessions/registry.py src/ecommerce_agent/sessions/factory.py src/ecommerce_agent/api/app.py tests/test_runtime_actor.py tests/test_session_registry.py tests/test_session_factory.py
git commit -m "feat(sessions): actor-bound runtime with owner + spring_user_id"
```

---

## Task 9: Per-actor approval client (`X-User-Id`)

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py` (update + add)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sessions_api.py  (add)
from ecommerce_agent.api.sessions import _approval_client
from ecommerce_agent.auth.models import Actor, Role
from ecommerce_agent.config import Settings


def test_approval_client_uses_actor_spring_user_id(monkeypatch):
    captured = {}

    def fake_make(settings, *, session_id, user_id):
        captured["session_id"] = session_id
        captured["user_id"] = user_id
        return object()

    monkeypatch.setattr("ecommerce_agent.api.sessions.make_approval_client", fake_make)

    request = type("R", (), {})()
    request.app = type("A", (), {})()
    request.app.state = type("S", (), {})()
    request.app.state.settings = Settings(_env_file=None)
    request.app.state.approval_clients = {}
    request.app.state.approval_client_factory = None

    actor = Actor(user_id="alice", username="alice", role=Role.OPERATOR, spring_user_id=42)
    _approval_client(request, "sess-1", actor)
    assert captured == {"session_id": "sess-1", "user_id": "42"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sessions_api.py::test_approval_client_uses_actor_spring_user_id -v`
Expected: FAIL (`_approval_client` takes 2 args; doesn't pass `user_id`).

- [ ] **Step 3: Implement**

In `src/ecommerce_agent/api/sessions.py`, update `_approval_client` to accept the actor and thread its spring id:
```python
def _approval_client(request: Request, session_id: str, actor: Actor) -> Any:
    factory = getattr(request.app.state, "approval_client_factory", None)
    if callable(factory):
        return factory(session_id)
    user_id = str(actor.spring_user_id)
    clients = getattr(request.app.state, "approval_clients", None)
    if isinstance(clients, dict):
        client = clients.get(session_id)
        if client is None:
            client = make_approval_client(
                request.app.state.settings, session_id=session_id, user_id=user_id
            )
            clients[session_id] = client
        return client
    return make_approval_client(request.app.state.settings, session_id=session_id, user_id=user_id)
```

Update `make_approval_client` in `src/ecommerce_agent/approvals.py` to accept `user_id`:
```python
def make_approval_client(settings: Settings, *, session_id: str, user_id: str | None = None) -> ApprovalClient:
    return ApprovalClient.from_settings(settings, session_id=session_id, user_id=user_id)
```

Update `approve_approval` and `reject_approval` to depend on `current_actor`, owner-check, and pass the actor:
```python
@router.post("/{session_id}/approvals/{approval_id}/approve")
async def approve_approval(
    session_id: str,
    approval_id: str,
    request: Request,
    actor: Actor = Depends(require(Action.APPROVE)),
) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    client = _approval_client(request, session_id, actor)
    actor_id = actor.user_id
    # ... rest unchanged (it already uses actor_id) ...
```
Apply the same three changes to `reject_approval` (dep `require(Action.APPROVE)`, `_require_owned_session`, `_approval_client(request, session_id, actor)`, `actor_id = actor.user_id`). Remove the old `actor_id = request.app.state.settings.spring_mcp_user_id` lines.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: PASS. Update any existing approval tests that called `_approval_client(request, session_id)` to pass an `Actor`, and log in (or set a cookie) for the approve/reject endpoint tests since they now require `Action.APPROVE`.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py src/ecommerce_agent/approvals.py tests/test_sessions_api.py
git commit -m "feat(approvals): per-actor X-User-Id and APPROVE gate on approve/reject"
```

---

## Task 10: Role-shaped runtime (viewers cannot propose)

**Files:**
- Modify: `src/ecommerce_agent/sessions/factory.py`
- Test: `tests/test_role_shaped_runtime.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_role_shaped_runtime.py
from ecommerce_agent.sessions.factory import RoutedSessionAgent, build_role_shaped_agents


class _Spy:
    def __init__(self, name):
        self.name = name
        self.called = False

    async def astream_events(self, inputs, config, version):
        self.called = True
        yield {"event": "on_chat_model_stream", "data": {"chunk": type("C", (), {"content": "x"})()}}


def test_viewer_agents_exclude_order_manager():
    analyst, order_manager = _Spy("a"), _Spy("om")
    agents = build_role_shaped_agents(analyst, order_manager, can_propose=False)
    assert "order-manager" not in agents
    assert "sales-analyst" in agents

    agents_op = build_role_shaped_agents(analyst, order_manager, can_propose=True)
    assert set(agents_op) == {"sales-analyst", "order-manager"}


async def test_router_denies_unavailable_specialist_without_delegating():
    analyst = _Spy("a")

    class _Router:
        async def route(self, text, history):
            return type("D", (), {"specialist": "order-manager", "source": "test", "reason": "write"})()

    routed = RoutedSessionAgent(
        router=_Router(),
        agents={"sales-analyst": analyst},
        default_specialist="sales-analyst",
    )
    events = [e async for e in routed.astream_events({"messages": [{"role": "user", "content": "make a PO"}]}, config={}, version="v2")]
    assert analyst.called is False
    kinds = [e["event"] for e in events]
    assert "on_policy_denied" in kinds
    assert any(e["event"] == "on_chat_model_stream" for e in events)  # a denial answer is streamed
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_role_shaped_runtime.py -v`
Expected: FAIL (`build_role_shaped_agents` missing; `RoutedSessionAgent` falls back instead of denying).

- [ ] **Step 3: Implement role-shaping + denial in the factory**

In `src/ecommerce_agent/sessions/factory.py`:

Add a helper and a denial message constant:
```python
POLICY_DENIED_MESSAGE = (
    "This request would create an operational change, which your role is not permitted to propose. "
    "Ask an operator to perform write actions."
)


def build_role_shaped_agents(analyst_agent, order_manager_agent, *, can_propose: bool) -> dict[str, Any]:
    agents: dict[str, Any] = {"sales-analyst": analyst_agent}
    if can_propose:
        agents["order-manager"] = order_manager_agent
    return agents
```

Update `RoutedSessionAgent.astream_events` so an unavailable specialist is denied, not silently re-routed:
```python
        selected = self.agents.get(decision.specialist)
        if selected is None:
            yield {
                "event": "on_policy_denied",
                "data": {"specialist": decision.specialist, "reason": "role_not_permitted"},
            }
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": SimpleNamespace(content=POLICY_DENIED_MESSAGE)},
            }
            return
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event
```
Add `from types import SimpleNamespace` to the factory imports.

In `build_session_runtime`, replace the hardcoded `agents={...}` with the role-shaped map:
```python
    routed_agent = RoutedSessionAgent(
        router=ClassifierRouter(get_classifier_model(settings), registry),
        agents=build_role_shaped_agents(analyst_agent, order_manager_agent, can_propose=actor.can_propose),
        default_specialist=registry.default.name,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_role_shaped_runtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/factory.py tests/test_role_shaped_runtime.py
git commit -m "feat(sessions): role-shaped runtime; deny write specialist for non-proposers"
```

---

## Task 11: Audit query API

**Files:**
- Create: `src/ecommerce_agent/audit/__init__.py`, `query.py`, `mongo.py`
- Create: `src/ecommerce_agent/api/audit.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_audit.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_audit.py
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


def _msgs():
    return [
        ThreadMessage(session_id="s1", type="user", content="hi", actor_id="alice", seq=1),
        ThreadMessage(
            session_id="s2", type="execution_result", content="done",
            actor_id="bob", approval_id="ap-9", seq=1,
        ),
    ]


async def test_in_memory_audit_filters():
    store = InMemoryAuditStore(_msgs())
    by_actor = await store.search(AuditQuery(actor_id="alice"))
    assert [m.session_id for m in by_actor] == ["s1"]
    by_approval = await store.search(AuditQuery(approval_id="ap-9"))
    assert [m.actor_id for m in by_approval] == ["bob"]
    by_type = await store.search(AuditQuery(type="execution_result"))
    assert [m.session_id for m in by_type] == ["s2"]


async def _build(role: Role):
    app = FastAPI()
    app.state.settings = Settings(_env_file=None)
    app.state.user_store = InMemoryUserStore()
    app.state.login_session_store = InMemoryLoginSessionStore()
    app.state.audit_store = InMemoryAuditStore(_msgs())
    await app.state.user_store.create(
        User(user_id="u1", username="u", password_hash=hash_password("pw"),
             role=role, spring_user_id=1, created_at="2026-06-13T00:00:00+00:00")
    )
    app.include_router(auth_router)
    app.include_router(audit_router)
    return app


async def test_audit_endpoint_operator_only():
    viewer_app = await _build(Role.VIEWER)
    with TestClient(viewer_app) as client:
        client.post("/api/auth/login", json={"username": "u", "password": "pw"})
        assert client.get("/api/audit/messages").status_code == 403

    op_app = await _build(Role.OPERATOR)
    with TestClient(op_app) as client:
        client.post("/api/auth/login", json={"username": "u", "password": "pw"})
        resp = client.get("/api/audit/messages", params={"actor_id": "bob"})
        assert resp.status_code == 200
        assert resp.json()["messages"][0]["approval_id"] == "ap-9"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_audit.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement query + in-memory store**

```python
# src/ecommerce_agent/audit/__init__.py  (empty)
```

```python
# src/ecommerce_agent/audit/query.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ecommerce_agent.threads.messages import ThreadMessage


@dataclass
class AuditQuery:
    actor_id: str | None = None
    approval_id: str | None = None
    session_id: str | None = None
    type: str | None = None
    since: str | None = None  # ISO-8601 inclusive lower bound on created_at
    until: str | None = None  # ISO-8601 exclusive upper bound on created_at
    limit: int = 100


class AuditStore(Protocol):
    async def search(self, query: AuditQuery) -> list[ThreadMessage]: ...


class InMemoryAuditStore:
    def __init__(self, messages: list[ThreadMessage] | None = None) -> None:
        self._messages = list(messages or [])

    async def search(self, query: AuditQuery) -> list[ThreadMessage]:
        results = [m for m in self._messages if _matches(m, query)]
        results.sort(key=lambda m: m.created_at, reverse=True)
        return results[: query.limit]


def _matches(m: ThreadMessage, q: AuditQuery) -> bool:
    if q.actor_id is not None and m.actor_id != q.actor_id:
        return False
    if q.approval_id is not None and m.approval_id != q.approval_id:
        return False
    if q.session_id is not None and m.session_id != q.session_id:
        return False
    if q.type is not None and m.type != q.type:
        return False
    if q.since is not None and m.created_at < q.since:
        return False
    if q.until is not None and m.created_at >= q.until:
        return False
    return True
```

- [ ] **Step 4: Implement the Mongo store**

```python
# src/ecommerce_agent/audit/mongo.py
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.audit.query import AuditQuery
from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


class MongoAuditStore:
    """Read-only cross-session view over the thread-messages collection."""

    def __init__(self, *, messages: Any, client: Any | None = None) -> None:
        self._messages = messages
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoAuditStore":
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(messages=db["thread_messages"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._messages.create_index("actor_id")
        await self._messages.create_index("approval_id")
        await self._messages.create_index("created_at")

    async def search(self, query: AuditQuery) -> list[ThreadMessage]:
        mongo_query: dict[str, Any] = {}
        if query.actor_id is not None:
            mongo_query["actor_id"] = query.actor_id
        if query.approval_id is not None:
            mongo_query["approval_id"] = query.approval_id
        if query.session_id is not None:
            mongo_query["session_id"] = query.session_id
        if query.type is not None:
            mongo_query["type"] = query.type
        created: dict[str, Any] = {}
        if query.since is not None:
            created["$gte"] = query.since
        if query.until is not None:
            created["$lt"] = query.until
        if created:
            mongo_query["created_at"] = created
        cursor = self._messages.find(mongo_query).sort("created_at", -1).limit(query.limit)
        return [
            ThreadMessage(**{k: v for k, v in doc.items() if k not in ("_id", "expire_at")})
            async for doc in cursor
        ]
```

- [ ] **Step 5: Implement the API router**

```python
# src/ecommerce_agent/api/audit.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ecommerce_agent.audit.query import AuditQuery
from ecommerce_agent.auth.dependencies import require
from ecommerce_agent.auth.models import Action, Actor

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/messages")
async def search_messages(
    request: Request,
    actor_id: str | None = None,
    approval_id: str | None = None,
    session: str | None = None,
    type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    _actor: Actor = Depends(require(Action.AUDIT_SEARCH)),
) -> dict:
    query = AuditQuery(
        actor_id=actor_id,
        approval_id=approval_id,
        session_id=session,
        type=type,
        since=since,
        until=until,
        limit=min(limit, 500),
    )
    messages = await request.app.state.audit_store.search(query)
    return {"messages": [m.model_dump() for m in messages]}
```

- [ ] **Step 6: Wire into the app**

In `src/ecommerce_agent/api/app.py`:
- import: `from ecommerce_agent.api.audit import router as audit_router` and `from ecommerce_agent.audit.mongo import MongoAuditStore`.
- in `create_app`: `app.state.audit_store = None` and `app.include_router(audit_router)`.
- in `lifespan` (after login-session wiring): `app.state.audit_store = getattr(app.state, "audit_store", None) or MongoAuditStore.from_settings(settings)` then `await app.state.audit_store.ensure_indexes()`; add it to the `finally` close loop.

- [ ] **Step 7: Run to verify pass**

Run: `uv run pytest tests/test_audit.py tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/ecommerce_agent/audit/ src/ecommerce_agent/api/audit.py src/ecommerce_agent/api/app.py tests/test_audit.py
git commit -m "feat(audit): operator-only cross-session audit query API"
```

---

## Task 12: Retention (TTL index + expire_at on threads and traces)

**Files:**
- Modify: `src/ecommerce_agent/threads/mongo.py`
- Modify: `src/ecommerce_agent/trace/mongo.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_retention.py` (new)

- [ ] **Step 1: Write the failing tests (fake Mongo collection)**

```python
# tests/test_retention.py
from datetime import UTC, datetime

from ecommerce_agent.threads.mongo import MongoThreadStore
from ecommerce_agent.threads.messages import ThreadMessage


class _FakeCounters:
    async def find_one_and_update(self, *a, **k):
        return {"seq": 1}


class _FakeMessages:
    def __init__(self):
        self.inserted = []
        self.indexes = []

    async def insert_one(self, doc):
        self.inserted.append(doc)

    async def create_index(self, *a, **k):
        self.indexes.append((a, k))


async def test_append_sets_expire_at_in_the_future():
    messages = _FakeMessages()
    store = MongoThreadStore(messages=messages, counters=_FakeCounters(), retention_days=90)
    await store.append(ThreadMessage(session_id="s1", type="user", content="hi"))
    doc = messages.inserted[0]
    assert "expire_at" in doc
    assert isinstance(doc["expire_at"], datetime)
    assert doc["expire_at"] > datetime.now(UTC)


async def test_ensure_indexes_creates_ttl_index():
    messages = _FakeMessages()
    store = MongoThreadStore(messages=messages, counters=_FakeCounters(), retention_days=90)
    await store.ensure_indexes()
    ttl = [(a, k) for (a, k) in messages.indexes if k.get("expireAfterSeconds") == 0]
    assert any(a == ("expire_at",) for a, _ in ttl)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_retention.py -v`
Expected: FAIL (`MongoThreadStore.__init__` has no `retention_days`; no `expire_at`; no `ensure_indexes`).

- [ ] **Step 3: Implement retention in the thread store**

In `src/ecommerce_agent/threads/mongo.py`:

Add imports: `from datetime import UTC, datetime, timedelta`.

Constructor + `from_settings`:
```python
    def __init__(
        self, *, messages: Any, counters: Any, client: Any | None = None, retention_days: int = 90
    ) -> None:
        self._messages = messages
        self._counters = counters
        self._client = client
        self._retention_days = retention_days

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoThreadStore":
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(
            messages=db["thread_messages"],
            counters=db["thread_counters"],
            client=client,
            retention_days=settings.audit_retention_days,
        )

    async def ensure_indexes(self) -> None:
        await self._messages.create_index("expire_at", expireAfterSeconds=0)
        await self._messages.create_index("actor_id")
        await self._messages.create_index("approval_id")
        await self._messages.create_index("created_at")
```

`append` — add `expire_at` to the inserted doc:
```python
    async def append(self, message: ThreadMessage) -> ThreadMessage:
        counter = await self._counters.find_one_and_update(
            {"_id": message.session_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        stored = message.model_copy(update={"seq": counter["seq"]})
        doc = stored.model_dump()
        doc["expire_at"] = datetime.now(UTC) + timedelta(days=self._retention_days)
        await self._messages.insert_one(doc)
        return stored
```

`list_messages` and `latest_message` — exclude `expire_at` when rebuilding (it is not a `ThreadMessage` field). Change both comprehensions/dicts from `if key != "_id"` to `if key not in ("_id", "expire_at")`.

- [ ] **Step 4: Apply the same pattern to the trace store**

In `src/ecommerce_agent/trace/mongo.py`: add `retention_days` to `__init__`/`from_settings`, set `expire_at = datetime.now(UTC) + timedelta(days=self._retention_days)` on the persisted doc in `save`, add `ensure_indexes` creating the `expire_at` TTL index, and strip `expire_at` when reconstructing the `TraceRecord`. (Mirror the thread-store edits; read the file first to match its exact `save`/`get` shape.)

- [ ] **Step 5: Wire `ensure_indexes` in the app**

In `src/ecommerce_agent/api/app.py` `lifespan`, after the stores are constructed, call `ensure_indexes()` on `thread_store` and `trace_store` if present (same `getattr`-callable guard used for the auth stores).

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_retention.py tests/test_mongo_thread_store.py tests/test_mongo_trace_store.py -v`
Expected: PASS (update the existing Mongo store tests for the new constructor kwarg / stripped field as needed).

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/threads/mongo.py src/ecommerce_agent/trace/mongo.py src/ecommerce_agent/api/app.py tests/test_retention.py tests/test_mongo_thread_store.py tests/test_mongo_trace_store.py
git commit -m "feat(retention): expire_at TTL index on thread messages and traces"
```

---

## Task 13: CLI `users add` seed command

**Files:**
- Modify: `src/ecommerce_agent/cli.py`
- Test: `tests/test_cli.py` (add)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (add)
from ecommerce_agent.cli import build_parser


def test_users_add_parser():
    parser = build_parser()
    args = parser.parse_args(["users", "add", "--username", "alice", "--role", "operator", "--spring-user-id", "7"])
    assert args.command == "users"
    assert args.username == "alice"
    assert args.role == "operator"
    assert args.spring_user_id == 7


def test_users_add_creates_user(monkeypatch):
    import ecommerce_agent.cli as cli

    created = {}

    class _Store:
        @classmethod
        def from_settings(cls, settings):
            return cls()

        async def ensure_indexes(self):
            pass

        async def create(self, user):
            created["user"] = user

        def close(self):
            pass

    monkeypatch.setattr(cli, "MongoUserStore", _Store, raising=False)
    monkeypatch.setattr(cli, "_prompt_password", lambda: "pw")

    parser = build_parser()
    args = parser.parse_args(["users", "add", "--username", "alice", "--role", "operator", "--spring-user-id", "7"])
    args.func(args)

    assert created["user"].username == "alice"
    assert created["user"].role == "operator"
    assert created["user"].spring_user_id == 7
    assert created["user"].password_hash.startswith("$argon2")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -k users -v`
Expected: FAIL (no `users` subcommand).

- [ ] **Step 3: Implement**

In `src/ecommerce_agent/cli.py`:

Top-level imports:
```python
import getpass
import uuid
from datetime import UTC, datetime

from ecommerce_agent.auth.users_store import MongoUserStore
```

In `build_parser`, after the `eval_parser` block:
```python
    users_parser = subparsers.add_parser("users", help="Manage users")
    users_sub = users_parser.add_subparsers(dest="users_command", required=True)
    add_parser = users_sub.add_parser("add", help="Create a user")
    add_parser.add_argument("--username", required=True)
    add_parser.add_argument("--role", required=True, choices=["viewer", "operator"])
    add_parser.add_argument("--spring-user-id", dest="spring_user_id", type=int, required=True)
    add_parser.set_defaults(func=run_users_command)
```

Add the command + helper:
```python
def _prompt_password() -> str:
    return getpass.getpass("Password: ")


def run_users_command(args: argparse.Namespace) -> None:
    import asyncio

    from ecommerce_agent.auth.models import Role, User
    from ecommerce_agent.auth.passwords import hash_password
    from ecommerce_agent.config import get_settings

    password = _prompt_password()
    user = User(
        user_id=uuid.uuid4().hex,
        username=args.username,
        password_hash=hash_password(password),
        role=Role(args.role),
        spring_user_id=args.spring_user_id,
        created_at=datetime.now(UTC).isoformat(),
    )
    store = MongoUserStore.from_settings(get_settings())

    async def _run() -> None:
        await store.ensure_indexes()
        await store.create(user)

    try:
        asyncio.run(_run())
    finally:
        store.close()
    print(f"created user {user.username} ({user.role})")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/cli.py tests/test_cli.py
git commit -m "feat(cli): users add seed command"
```

---

## Task 14: Frontend auth shell

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx` (+ a small `Login` component)
- Test: `frontend/src/__tests__/auth.test.tsx` (path follows the existing frontend test convention)

> **Before starting:** read `frontend/src/api/client.ts` and `frontend/src/App.tsx` to match the existing fetch helper, React Query setup, and test framework (Vitest/RTL vs. Jest). The snippets below are the required behavior; adapt to the established patterns rather than introducing new ones. Run the frontend suite with the project's existing command (check `frontend/package.json` `scripts.test`).

- [ ] **Step 1: Add auth API helpers**

In `frontend/src/api/client.ts`, ensure all fetches send cookies (`credentials: "include"`) and add:
```typescript
export interface Me {
  user_id: string;
  username: string;
  role: "viewer" | "operator";
  spring_user_id: number;
}

export async function getMe(): Promise<Me | null> {
  const resp = await fetch("/api/auth/me", { credentials: "include" });
  if (resp.status === 401) return null;
  if (!resp.ok) throw new Error(`me failed: ${resp.status}`);
  return resp.json();
}

export async function login(username: string, password: string): Promise<Me> {
  const resp = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (resp.status === 401) throw new Error("invalid credentials");
  if (!resp.ok) throw new Error(`login failed: ${resp.status}`);
  return resp.json();
}

export async function logout(): Promise<void> {
  await fetch("/api/auth/logout", { method: "POST", credentials: "include" });
}
```

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/__tests__/auth.test.tsx  (adapt import paths/framework to the project)
import { render, screen, waitFor } from "@testing-library/react";
import App from "../App";

beforeEach(() => {
  globalThis.fetch = vi.fn(async (url: string) => {
    if (url === "/api/auth/me") return new Response(null, { status: 401 });
    return new Response("{}", { status: 200 });
  }) as any;
});

test("shows login form when unauthenticated", async () => {
  render(<App />);
  await waitFor(() => expect(screen.getByLabelText(/username/i)).toBeInTheDocument());
});
```

- [ ] **Step 3: Run to verify failure**

Run the frontend test command (e.g. `cd frontend && npm test`).
Expected: FAIL (App does not render a login form on 401).

- [ ] **Step 4: Implement the auth shell**

In `frontend/src/App.tsx`: on mount call `getMe()`. While loading show a spinner; if it returns `null`, render a `Login` form (username/password → `login()` → refetch `me`); otherwise render the existing session console plus a logout control that calls `logout()`, clears React Query/session state, and returns to the login form. API helpers that hit a session endpoint should treat a 401 response as "session revoked" → return to login.

- [ ] **Step 5: Run to verify pass**

Run the frontend test command.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/App.tsx frontend/src/__tests__/auth.test.tsx
git commit -m "feat(frontend): minimal auth shell (login/logout/me, 401 recovery)"
```

---

## Task 15: Cross-repo — Java cross-actor denial test + spec doc

**Files (in `../ecommerce-mcp-server`):**
- Modify: `src/test/java/com/ecommerce/agent/controller/ApprovalControllerTest.java`
- Modify: `docs/2026-06-05-ecommerce-mcp-server-spec.md`

> The Python side already sends the real `spring_user_id` (Task 9). This task locks the backend guarantee and documents the trust boundary. Read `ApprovalControllerTest.java` first to match its MockMvc/WebMvc setup and header helpers.

- [ ] **Step 1: Add the failing cross-actor denial test**

Add a test asserting that actor B (different `X-User-Id`, valid `X-Service-Token`) is denied actor A's approval on `GET /approvals/{id}`, `POST /approvals/{id}/approve`, and `POST /approvals/{id}/execute`. Expected: `GET` returns 404 (filtered by `isSameActor`); `approve` returns 409/denied (no transition for a non-owner); `execute` does not consume A's approval. Use the existing test's pattern for seeding an approval owned by user A and sending requests with user B's headers.

- [ ] **Step 2: Run to verify it passes (binding already exists) or fails (gap found)**

Run (in the Java repo): `./mvnw test -Dtest=ApprovalControllerTest`
Expected: PASS if `isSameActor` already covers all three; if any path leaks, that is a real gap — fix the controller/service to enforce `isSameActor` before acting, then re-run to green.

- [ ] **Step 3: Document the trust boundary**

In `docs/2026-06-05-ecommerce-mcp-server-spec.md`, add a short subsection to the auth/identity section: the FastAPI gateway authenticates humans (HttpOnly session cookie); Spring trusts the `X-Service-Token` and binds to the asserted `X-User-Id`/`X-Session-Id`; approval records are owned by `(userId, sessionId)` and every read/transition/execute is actor-scoped via `isSameActor`; per-actor `X-User-Id` now flows from the real authenticated operator (no fixed `"1"`).

- [ ] **Step 4: Commit (in the Java repo)**

```bash
cd ../ecommerce-mcp-server
git add src/test/java/com/ecommerce/agent/controller/ApprovalControllerTest.java docs/2026-06-05-ecommerce-mcp-server-spec.md
git commit -m "test(approval): cross-actor denial; doc(spec): gateway trust boundary"
```

---

## Task 16: Full-suite verification

- [ ] **Step 1: Python tests**

Run: `uv run pytest -q`
Expected: all pass (excluding `integration`/`docker`/`live` markers, which skip without their services).

- [ ] **Step 2: Lint**

Run: `uv run ruff check src tests`
Expected: clean.

- [ ] **Step 3: Frontend**

Run the frontend lint + test commands from `frontend/package.json`.
Expected: clean / pass.

- [ ] **Step 4: Java (cross-repo)**

Run (in `../ecommerce-mcp-server`): `./mvnw test`
Expected: pass, including the new cross-actor test.

- [ ] **Step 5: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test(m4): slice 5 full-suite verification fixups"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- §2.1 browser↔FastAPI auth → Tasks 1–6. §2.2/§5 per-actor `X-User-Id` (MCP + REST) → Tasks 8, 9. §2.3/§5.1 role-shaped runtime → Task 10. §3.1–3.5 models/passwords/stores/permissions → Tasks 1–3. §3.6 deps + router → Tasks 5–6. §3.7 frontend shell → Task 14. §4 isolation → Tasks 7–8. §6 audit API → Task 11. §7 retention → Task 12. §8 cross-repo → Task 15. §9 CLI → Task 13. §10 error handling (401/403/404, generic login) → Tasks 5, 6, 7, 9. §11 risks (migration/backfill) → seeding via Task 13; backfill noted below.
- **Migration backfill (R-B):** seed an operator via `users add` (Task 13). Legacy ownerless sessions: not auto-migrated by a task — they simply won't match any owner and become inaccessible (the spec's accepted default alternative). If backfill-to-seed-owner is preferred at execution time, add a one-off script before go-live; flagged here so it isn't silently dropped.

**Placeholder scan:** no TBD/TODO; code shown for every code step. Frontend (Task 14) and Java (Task 15) steps describe required behavior with concrete snippets but defer to the existing project patterns by design (their exact frameworks/helpers must be read first) — these are the two tasks an executor should open the target files for before writing.

**Type consistency:** `RuntimeActor(user_id, spring_user_id, can_propose)`, `Actor(user_id, username, role, spring_user_id)`, `User(... spring_user_id, created_at)`, `AuditQuery(actor_id, approval_id, session_id, type, since, until, limit)`, `can(role, action)`, `_approval_client(request, session_id, actor)`, `make_approval_client(settings, *, session_id, user_id=None)`, `build_session_runtime(session_id, settings, actor)`, `SessionRegistry.create(actor)` / `get_or_create_runtime(session_id, actor, session_known)` are used consistently across tasks.

**Known cross-task red:** `tests/test_session_isolation.py` (Task 7) depends on the registry/runtime signature from Task 8 and stays red until Task 8 lands — called out in Task 7 Step 6.
