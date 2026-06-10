# M3 Phase 1 — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the backend the operator console needs — durable session records + list/get endpoints, session-existence validation (404), runtime rehydration, a side-effect-free single-turn guard, `/health` component checks, and dev/test-safe SPA static serving — over the existing M2 session/thread API.

**Architecture:** A new `SessionStore` (async protocol; `InMemorySessionStore` for tests, `MongoSessionStore` for prod) persists `{session_id, title, created_at}` so sessions survive restarts and are the authoritative existence check. The `SessionRegistry` gains `get_or_create_runtime` (rehydrate a known session, building outside the lock and closing a duplicate-rebuild loser) and a per-session turn marker (`try_begin_turn`/`end_turn`). `post_message` acquires the turn marker before any side effect. `/health` gains lightweight `components` (mongo ping, sandbox docker ping, model config-only). The SPA is mounted only if its build dir exists.

**Tech Stack:** FastAPI, motor (Mongo), pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`), TestClient.

**Spec:** [docs/2026-06-10-m3-operator-console-design.md](../2026-06-10-m3-operator-console-design.md) §3, §5 (binding), §7. The React SPA is a separate plan; this one produces a fully tested API the SPA builds on.

**Conventions (from the codebase):** tests live flat in `tests/`, use `Settings(_env_file=None, **overrides)`, `fastapi.testclient.TestClient`, fake stores/clients (see [tests/test_sessions_api.py](../../tests/test_sessions_api.py)). `from __future__ import annotations` atop new modules. Run: `uv run pytest -q`; lint: `uv run ruff check .` (line-length 100).

---

### Task 1: Add the `frontend_dist_dir` setting

**Files:**
- Modify: `src/ecommerce_agent/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_settings_expose_frontend_dist_dir() -> None:
    from ecommerce_agent.config import Settings

    assert Settings(_env_file=None).frontend_dist_dir == "frontend/dist"
    assert Settings(_env_file=None, frontend_dist_dir="/tmp/x").frontend_dist_dir == "/tmp/x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_settings_expose_frontend_dist_dir -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'frontend_dist_dir'`.

- [ ] **Step 3: Add the setting**

In `src/ecommerce_agent/config.py`, in the `Settings` class (after the M2 session settings block):

```python
    frontend_dist_dir: str = "frontend/dist"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/config.py tests/test_config.py
git commit -m "feat(m3): add frontend_dist_dir setting"
```

---

### Task 2: ThreadStore — `latest_message` and `count_messages`

**Files:**
- Modify: `src/ecommerce_agent/threads/store.py`
- Modify: `src/ecommerce_agent/threads/mongo.py`
- Test: `tests/test_thread_store.py`, `tests/test_mongo_thread_store.py`

The session-list endpoint needs a per-session last-message preview and count without loading the whole thread.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_thread_store.py`:

```python
@pytest.mark.asyncio
async def test_latest_message_and_count() -> None:
    store = InMemoryThreadStore()
    assert await store.latest_message("s1") is None
    assert await store.count_messages("s1") == 0

    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    latest = await store.latest_message("s1")
    assert latest is not None and latest.content == "b" and latest.seq == 2
    assert await store.count_messages("s1") == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_store.py::test_latest_message_and_count -v`
Expected: FAIL — `AttributeError: 'InMemoryThreadStore' object has no attribute 'latest_message'`.

- [ ] **Step 3: Extend the protocol and the in-memory store**

In `src/ecommerce_agent/threads/store.py`, add to the `ThreadStore` Protocol:

```python
    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        """Return the newest message for `session_id`, or None."""
        ...

    async def count_messages(self, session_id: str) -> int:
        """Return how many messages `session_id` has."""
        ...
```

And to `InMemoryThreadStore`:

```python
    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        async with self._lock:
            bucket = self._messages.get(session_id, ())
            return bucket[-1] if bucket else None

    async def count_messages(self, session_id: str) -> int:
        async with self._lock:
            return len(self._messages.get(session_id, ()))
```

- [ ] **Step 4: Add the Mongo implementation + its test**

In `src/ecommerce_agent/threads/mongo.py`, add to `MongoThreadStore`:

```python
    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        doc = await self._messages.find_one({"session_id": session_id}, sort=[("seq", -1)])
        if doc is None:
            return None
        return ThreadMessage(**{key: value for key, value in doc.items() if key != "_id"})

    async def count_messages(self, session_id: str) -> int:
        return await self._messages.count_documents({"session_id": session_id})
```

Add to `tests/test_mongo_thread_store.py` (extend the existing `FakeMessages` with `find_one`/`count_documents`):

```python
@pytest.mark.asyncio
async def test_mongo_latest_message_and_count() -> None:
    messages = FakeMessages()
    store = MongoThreadStore(messages=messages, counters=FakeCounters())
    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    latest = await store.latest_message("s1")
    assert latest is not None and latest.content == "b"
    assert await store.count_messages("s1") == 2
```

In that test file's `FakeMessages`, add:

```python
    async def find_one(self, filt, sort=None):
        docs = [d for d in self.docs if d["session_id"] == filt["session_id"]]
        if not docs:
            return None
        if sort:
            key, direction = sort[0]
            docs = sorted(docs, key=lambda d: d[key], reverse=direction < 0)
        return docs[0]

    async def count_documents(self, filt):
        return len([d for d in self.docs if d["session_id"] == filt["session_id"]])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_thread_store.py tests/test_mongo_thread_store.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/threads/store.py src/ecommerce_agent/threads/mongo.py tests/test_thread_store.py tests/test_mongo_thread_store.py
git commit -m "feat(m3): add latest_message/count_messages to ThreadStore"
```

---

### Task 3: `SessionStore` (records + summaries)

**Files:**
- Create: `src/ecommerce_agent/sessions/store.py`
- Test: `tests/test_session_store.py`, `tests/test_mongo_session_store.py`

A `SessionStore` persists `{session_id, title, created_at}`. `InMemorySessionStore` for tests; `MongoSessionStore` for prod. The session-list summary (with preview/count) is composed in the API layer (Task 5) from this store + the ThreadStore.

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_store.py`:

```python
import pytest

from ecommerce_agent.sessions.store import InMemorySessionStore


@pytest.mark.asyncio
async def test_create_get_exists_and_title() -> None:
    store = InMemorySessionStore()
    assert await store.exists("s1") is False

    await store.create("s1")
    assert await store.exists("s1") is True
    record = await store.get("s1")
    assert record is not None and record["session_id"] == "s1" and record["title"] is None
    assert isinstance(record["created_at"], str)


@pytest.mark.asyncio
async def test_set_title_if_absent_only_sets_once() -> None:
    store = InMemorySessionStore()
    await store.create("s1")
    await store.set_title_if_absent("s1", "first")
    await store.set_title_if_absent("s1", "second")
    assert (await store.get("s1"))["title"] == "first"


@pytest.mark.asyncio
async def test_list_records_newest_first() -> None:
    store = InMemorySessionStore()
    await store.create("old")
    await store.create("new")
    ids = [r["session_id"] for r in await store.list_records()]
    assert ids == ["new", "old"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.sessions.store'`.

- [ ] **Step 3: Write the store module**

Create `src/ecommerce_agent/sessions/store.py`:

```python
from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore(Protocol):
    async def create(self, session_id: str) -> None: ...
    async def exists(self, session_id: str) -> bool: ...
    async def get(self, session_id: str) -> dict[str, Any] | None: ...
    async def set_title_if_absent(self, session_id: str, title: str) -> None: ...
    async def list_records(self) -> list[dict[str, Any]]: ...


class InMemorySessionStore:
    """Async, test-only SessionStore."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._order = itertools.count()
        self._seq: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str) -> None:
        async with self._lock:
            if session_id in self._records:
                return
            self._records[session_id] = {
                "session_id": session_id,
                "title": None,
                "created_at": _now_iso(),
            }
            self._seq[session_id] = next(self._order)

    async def exists(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._records

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(session_id)
            return dict(record) if record else None

    async def set_title_if_absent(self, session_id: str, title: str) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is not None and record["title"] is None:
                record["title"] = title

    async def list_records(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                dict(record)
                for _, record in sorted(
                    self._records.items(),
                    key=lambda item: self._seq[item[0]],
                    reverse=True,
                )
            ]


class MongoSessionStore:
    """Source-of-truth SessionStore backed by MongoDB via motor."""

    def __init__(self, *, sessions: Any) -> None:
        self._sessions = sessions

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoSessionStore:
        db = AsyncIOMotorClient(settings.mongo_url)[settings.mongo_db]
        return cls(sessions=db["sessions"])

    async def create(self, session_id: str) -> None:
        await self._sessions.update_one(
            {"_id": session_id},
            {"$setOnInsert": {"title": None, "created_at": _now_iso()}},
            upsert=True,
        )

    async def exists(self, session_id: str) -> bool:
        return await self._sessions.count_documents({"_id": session_id}, limit=1) > 0

    async def get(self, session_id: str) -> dict[str, Any] | None:
        doc = await self._sessions.find_one({"_id": session_id})
        return self._to_record(doc) if doc else None

    async def set_title_if_absent(self, session_id: str, title: str) -> None:
        await self._sessions.update_one(
            {"_id": session_id, "title": None},
            {"$set": {"title": title}},
        )

    async def list_records(self) -> list[dict[str, Any]]:
        cursor = self._sessions.find().sort("created_at", -1)
        return [self._to_record(doc) async for doc in cursor]

    @staticmethod
    def _to_record(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": doc["_id"],
            "title": doc.get("title"),
            "created_at": doc.get("created_at"),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_store.py -v`
Expected: PASS.

- [ ] **Step 5: Add the Mongo store test (fake collection + gated integration)**

Create `tests/test_mongo_session_store.py`:

```python
import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.store import MongoSessionStore


class FakeSessions:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self._order: list[str] = []

    async def update_one(self, filt, update, upsert=False):
        sid = filt["_id"]
        if "$setOnInsert" in update:
            if sid not in self.docs:
                self.docs[sid] = {"_id": sid, **update["$setOnInsert"]}
                self._order.append(sid)
            return
        if "$set" in update:
            doc = self.docs.get(sid)
            if doc is not None and all(doc.get(k) == v for k, v in filt.items() if k != "_id"):
                doc.update(update["$set"])

    async def count_documents(self, filt, limit=None):
        return 1 if filt["_id"] in self.docs else 0

    async def find_one(self, filt):
        return self.docs.get(filt["_id"])

    def find(self):
        order = self._order

        class _Cursor:
            def sort(self, key, direction):
                return self

            def __aiter__(self_inner):
                async def gen():
                    for sid in reversed(order):
                        yield self.docs[sid]

                return gen()

        return _Cursor()


@pytest.mark.asyncio
async def test_mongo_session_store_create_title_list() -> None:
    store = MongoSessionStore(sessions=FakeSessions())
    await store.create("s1")
    await store.create("s1")  # idempotent
    assert await store.exists("s1") is True
    await store.set_title_if_absent("s1", "hello")
    await store.set_title_if_absent("s1", "ignored")
    assert (await store.get("s1"))["title"] == "hello"
    await store.create("s2")
    assert [r["session_id"] for r in await store.list_records()] == ["s2", "s1"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_mongo_session_store() -> None:
    if not os.environ.get("RUN_MONGO_INTEGRATION"):
        pytest.skip("set RUN_MONGO_INTEGRATION and run a local Mongo to exercise this")
    store = MongoSessionStore.from_settings(Settings(_env_file=None))
    sid = f"itest-{os.getpid()}"
    await store.create(sid)
    assert await store.exists(sid) is True
```

The fake-collection test runs in the default suite; the real-Mongo test is integration-gated and skips without `RUN_MONGO_INTEGRATION`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_store.py tests/test_mongo_session_store.py -v`
Expected: PASS (the real-Mongo test skips).

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/sessions/store.py tests/test_session_store.py tests/test_mongo_session_store.py
git commit -m "feat(m3): add SessionStore (in-memory + mongo)"
```

---

### Task 4: Registry — rehydration + single-turn guard

**Files:**
- Modify: `src/ecommerce_agent/sessions/registry.py`
- Test: `tests/test_session_registry.py`

`get_or_create_runtime` rebuilds a known session **outside** the global lock and closes a duplicate-rebuild loser. `try_begin_turn`/`end_turn` track one active turn per session.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_session_registry.py`:

```python
@pytest.mark.asyncio
async def test_get_or_create_rehydrates_known_session() -> None:
    built: list[str] = []

    async def build(session_id: str):
        built.append(session_id)
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    async def known(session_id: str) -> bool:
        return session_id == "known"

    runtime = await registry.get_or_create_runtime("known", known)
    assert runtime.session_id == "known"
    # cached now: a second call does not rebuild
    again = await registry.get_or_create_runtime("known", known)
    assert again is runtime
    assert built == ["known"]

    with pytest.raises(KeyError):
        await registry.get_or_create_runtime("ghost", known)


@pytest.mark.asyncio
async def test_try_begin_turn_enforces_single_turn() -> None:
    async def build(session_id: str):
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    assert await registry.try_begin_turn("s1") is True
    assert await registry.try_begin_turn("s1") is False  # already running
    await registry.end_turn("s1")
    assert await registry.try_begin_turn("s1") is True
```

(`make_runtime` already exists in this test file from M2.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_registry.py::test_get_or_create_rehydrates_known_session -v`
Expected: FAIL — `AttributeError: 'SessionRegistry' object has no attribute 'get_or_create_runtime'`.

- [ ] **Step 3: Implement on `SessionRegistry`**

In `src/ecommerce_agent/sessions/registry.py`, add `self._active_turns: set[str] = set()` to `__init__`, and add these methods:

```python
    async def get_or_create_runtime(
        self,
        session_id: str,
        session_known: Callable[[str], Awaitable[bool]],
    ) -> SessionRuntime:
        async with self._lock:
            cached = self._runtimes.get(session_id)
            if cached is not None:
                cached.touch()
                return cached
        # Build OUTSIDE the lock (Docker + MCP are slow) so one rebuild can't block others.
        if not await session_known(session_id):
            raise KeyError(session_id)
        runtime = await self._build_runtime(session_id)
        async with self._lock:
            winner = self._runtimes.get(session_id)
            if winner is not None:
                runtime.close()  # a concurrent rebuild won; don't leak this sandbox
                winner.touch()
                return winner
            self._make_room_locked()
            self._runtimes[session_id] = runtime
            return runtime

    async def try_begin_turn(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._active_turns:
                return False
            self._active_turns.add(session_id)
            return True

    async def end_turn(self, session_id: str) -> None:
        async with self._lock:
            self._active_turns.discard(session_id)
```

Ensure `Callable`/`Awaitable` are imported (they already are at the top of the file).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/registry.py tests/test_session_registry.py
git commit -m "feat(m3): registry rehydration + single-turn guard"
```

---

### Task 5: Session records wiring + `GET /api/sessions` + `GET /api/sessions/{id}`

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_sessions_api.py`

Wire a `session_store` into app state, write a record on `POST /api/sessions`, and add the list/get endpoints. (`build_test_app` in the test file gains `app.state.session_store = InMemorySessionStore()`.)

- [ ] **Step 1: Write the failing test**

In `tests/test_sessions_api.py`, add `from ecommerce_agent.sessions.store import InMemorySessionStore`, add `app.state.session_store = InMemorySessionStore()` inside `build_test_app`, and add:

```python
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
        assert meta["session_id"] == session_id and meta["message_count"] == 2


def test_get_unknown_session_404() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sessions_api.py::test_create_writes_record_and_list_returns_summary -v`
Expected: FAIL — the list endpoint/`session_store` don't exist yet (404 or AttributeError).

- [ ] **Step 3: Wire the store in `app.py`**

In `src/ecommerce_agent/api/app.py`: import `from ecommerce_agent.sessions.store import MongoSessionStore`; in `lifespan`, add
`app.state.session_store = getattr(app.state, "session_store", None) or MongoSessionStore.from_settings(settings)`;
and in `create_app`, add `app.state.session_store = None`.

- [ ] **Step 4: Add endpoints + record-on-create in `sessions.py`**

In `src/ecommerce_agent/api/sessions.py`, update `create_session` and add the list/get endpoints:

```python
def _title_from_message(message: str) -> str:
    return message.strip()[:80]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> dict[str, str]:
    session_id = await request.app.state.session_registry.create()
    await request.app.state.session_store.create(session_id)
    return {"session_id": session_id}


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    store = request.app.state.session_store
    thread_store = request.app.state.thread_store
    summaries = []
    for record in await store.list_records():
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


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request) -> dict[str, Any]:
    record = await request.app.state.session_store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        **record,
        "message_count": await request.app.state.thread_store.count_messages(session_id),
    }
```

> `GET /api/sessions` (list) must be registered so it is **not** shadowed by `GET /api/sessions/{session_id}`. With FastAPI, the literal-prefixed `@router.get("")` (path `/api/sessions`) and `@router.get("/{session_id}")` are distinct paths and resolve correctly regardless of declaration order; keep both as shown.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: PASS (existing tests still pass — they now set `session_store`).

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py src/ecommerce_agent/api/app.py tests/test_sessions_api.py
git commit -m "feat(m3): durable session records + list/get endpoints"
```

---

### Task 6: Session-existence validation (404) on every session-scoped endpoint

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

`GET …/thread`, `GET …/stream`, and the approval endpoints must 404 for an unknown session instead of serving an empty thread/stream. (`POST …/messages` is covered in Task 7 via rehydration.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sessions_api.py`:

```python
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
        assert response.status_code == 200 and response.json()["messages"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sessions_api.py::test_thread_and_approval_endpoints_404_unknown_session -v`
Expected: FAIL — `GET …/thread` returns 200 with empty messages; approve returns non-404.

- [ ] **Step 3: Add a validation helper and apply it**

In `src/ecommerce_agent/api/sessions.py`, add:

```python
async def _require_session(request: Request, session_id: str) -> None:
    if not await request.app.state.session_store.exists(session_id):
        raise HTTPException(status_code=404, detail="session not found")
```

Call `await _require_session(request, session_id)` as the first line of `get_thread`, `approve_approval`, and `reject_approval`. For the stream, add it at the top of the `stream` endpoint (before constructing the `EventSourceResponse`):

```python
@router.get("/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    await _require_session(request, session_id)
    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    return EventSourceResponse(_session_events(session_id, request, store, bus))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m3): 404 unknown sessions on session-scoped endpoints"
```

---

### Task 7: `post_message` — side-effect-free turn guard + rehydration + title

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

Acquire the per-session turn marker **before** any side effect; rehydrate the runtime via `get_or_create_runtime`; set the title from the first user message; release the marker when the turn finishes.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sessions_api.py` — async tests call the endpoint function directly with a
`SimpleNamespace(app=app)` request, mirroring `test_approve_endpoint_publishes_execution_result_to_session_bus`:

```python
@pytest.mark.asyncio
async def test_second_concurrent_send_409_is_side_effect_free() -> None:
    from fastapi import HTTPException

    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)
    # Simulate an in-flight turn by holding the marker.
    assert await app.state.session_registry.try_begin_turn(session_id) is True

    with pytest.raises(HTTPException) as exc:
        await post_message(
            session_id,
            MessageRequest(message="hi"),
            SimpleNamespace(app=app),  # type: ignore[arg-type]
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == {"error": "turn_in_progress"}
    # Side-effect-free: nothing appended to the thread.
    assert await app.state.thread_store.list_messages(session_id) == []


@pytest.mark.asyncio
async def test_message_to_reaped_session_rehydrates() -> None:
    from ecommerce_agent.api.sessions import MessageRequest, post_message

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)
    # Drop the in-memory runtime (simulate reap/restart); the session record remains.
    await app.state.session_registry.close_all()

    result = await post_message(
        session_id,
        MessageRequest(message="hi"),
        SimpleNamespace(app=app),  # type: ignore[arg-type]
    )
    assert "turn_id" in result  # rehydrated and accepted (would raise 404 otherwise)
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sessions_api.py::test_second_concurrent_send_409_is_side_effect_free -v`
Expected: FAIL — current `post_message` appends the user message before any guard and returns 202.

- [ ] **Step 3: Rewrite `post_message`**

Replace the body of `post_message` in `src/ecommerce_agent/api/sessions.py`:

```python
@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
) -> dict[str, Any]:
    registry = request.app.state.session_registry
    session_store = request.app.state.session_store

    async def _known(sid: str) -> bool:
        return await session_store.exists(sid)

    try:
        runtime = await registry.get_or_create_runtime(session_id, _known)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc

    # Acquire the per-session turn marker BEFORE any side effect.
    if not await registry.try_begin_turn(session_id):
        raise HTTPException(status_code=409, detail={"error": "turn_in_progress"})

    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    settings = request.app.state.settings
    turn_id = uuid.uuid4().hex
    try:
        user_message = await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="user",
                content=payload.message,
                turn_id=turn_id,
                actor_id="operator",
            ),
        )
        await session_store.set_title_if_absent(session_id, _title_from_message(payload.message))
    except Exception:
        await registry.end_turn(session_id)
        raise

    app_state = request.app.state

    async def run_and_record_trace() -> None:
        try:
            record = await run_turn(
                agent=runtime.agent,
                message=payload.message,
                session_id=session_id,
                turn_id=turn_id,
                store=store,
                bus=bus,
                recursion_limit=settings.agent_recursion_limit,
                approval_client=_approval_client(request, session_id),
            )
            trace_records = app_state.trace_records
            trace_records.setdefault(session_id, {})[turn_id] = record
            app_state.last_trace = record
        finally:
            await registry.end_turn(session_id)

    task = asyncio.create_task(run_and_record_trace())
    background_tasks = request.app.state.background_tasks
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return {"turn_id": turn_id, "user_message_id": user_message.message_id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: PASS (existing message tests still pass; the marker now clears in the task's `finally`).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m3): side-effect-free turn guard + rehydration + title in post_message"
```

---

### Task 8: `/health` component checks (mongo / sandbox / model — config-only)

**Files:**
- Create: `src/ecommerce_agent/api/health.py`
- Modify: `src/ecommerce_agent/threads/store.py`, `src/ecommerce_agent/threads/mongo.py` (add `ping`)
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_app.py`

The model check is **config-only** — it never makes a token-spending call.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_health_reports_components(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.health as health_module

    monkeypatch.setattr(health_module, "probe_sandbox", lambda settings: {"status": "ok"})

    app = create_app(settings=make_settings(llm_api_key="k"))
    # Inject a thread store with a ping (no real Mongo).
    from ecommerce_agent.threads.store import InMemoryThreadStore

    app.state.thread_store = InMemoryThreadStore()

    with TestClient(app) as client:
        body = client.get("/health").json()

    components = body["components"]
    assert components["mongo"]["status"] == "ok"
    assert components["sandbox"]["status"] == "ok"
    assert components["model"]["status"] == "ok"  # api key present, config-only


def test_health_model_unconfigured_without_key() -> None:
    app = create_app(settings=make_settings(llm_api_key=""))
    from ecommerce_agent.threads.store import InMemoryThreadStore

    app.state.thread_store = InMemoryThreadStore()
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["components"]["model"]["status"] == "unconfigured"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_health_reports_components -v`
Expected: FAIL — `components` is missing / `ecommerce_agent.api.health` doesn't exist.

- [ ] **Step 3: Add `ping` to the thread stores**

In `src/ecommerce_agent/threads/store.py` `InMemoryThreadStore`:

```python
    async def ping(self) -> bool:
        return True
```

In `src/ecommerce_agent/threads/mongo.py` `MongoThreadStore`:

```python
    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True
```

- [ ] **Step 4: Write the health module**

Create `src/ecommerce_agent/api/health.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

from ecommerce_agent.config import Settings


async def probe_mongo(thread_store: Any) -> dict[str, str]:
    try:
        ok = await thread_store.ping()
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok" if ok else "unavailable"}


def probe_sandbox(settings: Settings) -> dict[str, str]:
    try:
        import docker

        docker.from_env().ping()
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok"}


def probe_model(settings: Settings) -> dict[str, str]:
    # Config-only: never spend tokens on a health poll.
    if settings.llm_api_key and settings.llm_base_url:
        return {"status": "ok", "model": settings.llm_model, "checked": "config-only"}
    return {"status": "unconfigured"}


async def health_components(app_state: Any) -> dict[str, Any]:
    settings: Settings = app_state.settings
    sandbox = await asyncio.to_thread(probe_sandbox, settings)
    return {
        "mongo": await probe_mongo(app_state.thread_store),
        "sandbox": sandbox,
        "model": probe_model(settings),
    }
```

- [ ] **Step 5: Call it from `/health`**

In `src/ecommerce_agent/api/app.py`, import `from ecommerce_agent.api import health` and extend the `health()` handler to include components:

```python
    @app.get("/health")
    async def health_endpoint() -> dict[str, Any]:
        components = await health.health_components(app.state)
        return {
            "status": "ok",
            "app": app.state.settings.app_name,
            "environment": app.state.settings.environment,
            "configured_mcp_servers": configured_mcp_servers(app.state.settings),
            "agent_ready": app.state.session_registry is not None,
            "components": components,
        }
```

(Tests monkeypatch `health.probe_sandbox` so they don't require a Docker daemon.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/api/health.py src/ecommerce_agent/threads/store.py src/ecommerce_agent/threads/mongo.py src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "feat(m3): /health component checks (mongo/sandbox/model config-only)"
```

---

### Task 9: Dev/test-safe SPA static serving + route order

**Files:**
- Create: `src/ecommerce_agent/api/spa.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_app.py`

Mount the SPA only if `frontend_dist_dir` exists; serve `index.html` for unknown non-`/api`, non-`/health*`, non-`/assets` paths. API tests must pass with no `dist`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_app_starts_without_frontend_dist() -> None:
    app = create_app(settings=make_settings(frontend_dist_dir="/nonexistent/dist"))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        # No catch-all mounted: unknown path is a normal 404, not index.html.
        assert client.get("/some/spa/route").status_code == 404


def test_spa_served_with_dist_fixture(tmp_path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>console</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = create_app(settings=make_settings(frontend_dist_dir=str(dist)))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200            # API still wins
        assert client.get("/api/sessions/ghost").status_code in (404, 422)  # API, not index.html
        assert "<title>console" in client.get("/").text            # index.html
        assert "<title>console" in client.get("/some/spa/route").text  # SPA fallback
        assert client.get("/assets/app.js").status_code == 200     # static asset
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_spa_served_with_dist_fixture -v`
Expected: FAIL — no SPA mount; `/` and `/some/spa/route` return 404.

- [ ] **Step 3: Write the SPA mount helper**

Create `src/ecommerce_agent/api/spa.py`:

```python
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def mount_spa(app: FastAPI, dist_dir: str) -> None:
    """Mount the built SPA, but only if `dist_dir` exists (dev/test-safe)."""
    dist = Path(dist_dir)
    index = dist / "index.html"
    if not dist.is_dir() or not index.is_file():
        logger.warning("frontend dist %s not found; skipping SPA mount", dist_dir)
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str, request: Request) -> FileResponse:
        # API and health routes are registered before this catch-all, so they win.
        if full_path.startswith(("api/", "health")):
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(index)
```

- [ ] **Step 4: Call it last in `create_app`**

In `src/ecommerce_agent/api/app.py`: import `from ecommerce_agent.api.spa import mount_spa`; at the **end** of `create_app`, after `app.include_router(sessions_router)`, add:

```python
    mount_spa(app, app.state.settings.frontend_dist_dir)
    return app
```

(The catch-all is registered last, so `/api/*` and `/health*` resolve to their handlers first; `mount_spa` also guards `api/`/`health` paths defensively.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite + lint**

Run: `uv run pytest -q` then `uv run ruff check .`
Expected: PASS; no lint errors (integration/live/docker tests skip without their env).

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/api/spa.py src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "feat(m3): dev/test-safe SPA static serving with route order"
```

---

## Self-Review

**Spec coverage (§3 / §5 / §7):**
- §3.1 durable session records (`sessions` collection, title from first user message) → Tasks 3, 5, 7.
- §3.2 `GET /api/sessions` list (preview + count) → Tasks 2, 5.
- §3.3 `GET /api/sessions/{id}` (+404) → Task 5.
- §3.4 rehydration via `get_or_create_runtime` (build outside lock, close loser) → Tasks 4, 7.
- §3.5 session-existence validation (404) on thread/stream/messages/approve/reject → Tasks 6 (reads + approvals), 7 (messages via rehydration).
- §3.6 single in-flight turn, `409 {"error": "turn_in_progress"}`, side-effect-free → Tasks 4, 7.
- §3.7 `/health` components (mongo/sandbox/model config-only, no token spend) → Task 8.
- §3.8 dev/test-safe static serving + route order → Task 9.
- §5 approval↔session binding is enforced by Java (no FastAPI re-implementation); covered by the existing approval flow + the new `_require_session` 404 (Task 6). A cross-session-rejection test belongs in the live/integration suite against the real Java server (out of this unit-test plan; noted for the integration pass).

**Out of scope (frontend plan):** the React/Vite SPA (sidebar, conversation+stream, approval workspace, health panel), the SSE consumption + turn-finalization logic, and the card render contract — all consume this API.

**Placeholder scan:** no placeholders or sketches; every code step shows complete code, and commands have expected output.

**Type consistency:** `SessionStore` methods (`create/exists/get/set_title_if_absent/list_records`) are used identically in Tasks 3/5/7; `get_or_create_runtime(session_id, session_known)` and `try_begin_turn/end_turn` consistent in Tasks 4/7; `latest_message`/`count_messages`/`ping` on the thread store consistent in Tasks 2/5/8; `health.probe_sandbox`/`probe_model`/`probe_mongo`/`health_components` consistent in Task 8.

**Known follow-up:** the cross-session approval-rejection assertion (§5) and the real-Mongo paths run only in the integration/live suite; add them when wiring the end-to-end M3 demo.
