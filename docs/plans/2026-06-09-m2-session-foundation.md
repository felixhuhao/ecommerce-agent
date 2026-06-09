# M2 Phase 1 — Session Foundation (Thread + Stream + Sandbox) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace M1's stateless chat with first-class sessions: a server-owned MongoDB conversation thread, an in-process per-session event bus, a unified per-session SSE stream, and a per-session DockerSandbox — validated end-to-end with the existing sales-analyst agent.

**Architecture:** A pure `ThreadStore` (async protocol; `InMemoryThreadStore` for tests, `MongoThreadStore` for prod) persists append-only `ThreadMessage`s with a per-session monotonic `seq`. A `SessionBus` fans out stream events to per-session subscriber queues; an `append_and_publish` helper makes Mongo the source of truth and the bus best-effort. A `SessionRegistry` owns per-session runtimes (per-session MCP headers + per-session sandbox + agent) and reaps idle ones. The agent turn runs as a server task that publishes `token`/`tool` events live and appends a durable `agent_answer`. The SSE endpoint subscribes-first-then-replays to close the backlog/live race.

**Tech Stack:** FastAPI, sse-starlette, motor (new), DeepAgents, pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** [docs/2026-06-09-m2-approved-action-workflow-design.md](../2026-06-09-m2-approved-action-workflow-design.md) §3, §4, §10 step 2. This plan covers build-seq step 2 only; order-manager/coordinator (step 3), approval orchestration (step 4), and the integration loop (step 5) are separate plans.

**Conventions to follow (from the existing codebase):**
- Tests live flat in `tests/`, use plain `def`/`async def` functions, `pytest.MonkeyPatch`, `fastapi.testclient.TestClient`, and `Settings(_env_file=None, **overrides)` (see [tests/test_app.py](../../tests/test_app.py)).
- `from __future__ import annotations` at the top of new modules.
- ruff line-length 100, rules `E,F,I,UP,B`.
- Run the default suite with `uv run pytest -q`; lint with `uv run ruff check .`.

---

### Task 1: Add `motor` dependency and the new settings

**Files:**
- Modify: `pyproject.toml:9-19` (dependencies)
- Modify: `src/ecommerce_agent/config.py:29-46`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_settings_expose_m2_session_defaults() -> None:
    from ecommerce_agent.config import Settings

    settings = Settings(_env_file=None)

    assert settings.mongo_url == "mongodb://localhost:27017"
    assert settings.mongo_db == "ecommerce_agent"
    assert settings.approval_api_base_url == "http://localhost:8080"
    assert settings.session_idle_ttl_seconds == 1800
    assert settings.max_live_sessions == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_settings_expose_m2_session_defaults -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'mongo_url'`.

- [ ] **Step 3: Add the settings fields**

In `src/ecommerce_agent/config.py`, after the sandbox block (around line 46, before `get_settings`), add:

```python
    # M2 session / conversation thread
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "ecommerce_agent"
    approval_api_base_url: str = "http://localhost:8080"
    session_idle_ttl_seconds: int = Field(default=1800, gt=0)
    max_live_sessions: int = Field(default=50, gt=0)
```

- [ ] **Step 4: Add the dependency**

In `pyproject.toml`, add to the `dependencies` array (keep it sorted):

```toml
    "motor>=3.6.0",
```

Then run: `uv lock` (updates `uv.lock`).

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/ecommerce_agent/config.py tests/test_config.py
git commit -m "feat(m2): add motor dep and session settings"
```

---

### Task 2: `ThreadMessage` model

**Files:**
- Create: `src/ecommerce_agent/threads/__init__.py`
- Create: `src/ecommerce_agent/threads/messages.py`
- Test: `tests/test_thread_messages.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_thread_messages.py`:

```python
from ecommerce_agent.threads.messages import ThreadMessage


def test_thread_message_defaults_and_roundtrip() -> None:
    msg = ThreadMessage(session_id="s1", type="user", content="hello")

    assert msg.session_id == "s1"
    assert msg.type == "user"
    assert msg.seq == 0  # unassigned until the store appends
    assert len(msg.message_id) == 32  # uuid4 hex
    assert msg.created_at.endswith("+00:00")  # tz-aware iso

    dumped = msg.model_dump()
    assert dumped["approval_id"] is None
    assert ThreadMessage(**dumped) == msg


def test_thread_message_proposal_fields() -> None:
    msg = ThreadMessage(
        session_id="s1",
        type="agent_proposal",
        content="Proposed PO #123",
        approval_id="a1",
        tool_name="purchase_order_create",
        status="pending",
        card={"summary": "Restock 500"},
    )

    assert msg.approval_id == "a1"
    assert msg.card == {"summary": "Restock 500"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_messages.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.threads'`.

- [ ] **Step 3: Create the package and model**

Create `src/ecommerce_agent/threads/__init__.py` (empty file).

Create `src/ecommerce_agent/threads/messages.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageType = Literal[
    "user",
    "agent_answer",
    "agent_proposal",
    "approval_status",
    "execution_result",
]


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ThreadMessage(BaseModel):
    """One appended message in a session's conversation thread.

    `seq` is the per-session monotonic ordering key, assigned by the ThreadStore
    on append (0 means unassigned). Ordering and dedupe use `seq`, never `created_at`.
    """

    message_id: str = Field(default_factory=_new_id)
    session_id: str
    seq: int = 0
    type: MessageType
    content: str = ""
    created_at: str = Field(default_factory=_now_iso)

    # audit / correlation spine
    turn_id: str | None = None
    trace_id: str | None = None
    actor_id: str | None = None
    execution_id: str | None = None

    # type-specific
    approval_id: str | None = None
    card: dict[str, Any] | None = None
    tool_name: str | None = None
    status: str | None = None
    result: dict[str, Any] | None = None
    reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_messages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/threads/__init__.py src/ecommerce_agent/threads/messages.py tests/test_thread_messages.py
git commit -m "feat(m2): add ThreadMessage model with audit spine"
```

---

### Task 3: `ThreadStore` protocol + `InMemoryThreadStore`

**Files:**
- Create: `src/ecommerce_agent/threads/store.py`
- Test: `tests/test_thread_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_thread_store.py`:

```python
import pytest

from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import InMemoryThreadStore


@pytest.mark.asyncio
async def test_append_assigns_monotonic_seq_per_session() -> None:
    store = InMemoryThreadStore()

    a = await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    b = await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))
    other = await store.append(ThreadMessage(session_id="s2", type="user", content="c"))

    assert a.seq == 1
    assert b.seq == 2
    assert other.seq == 1  # seq is per-session


@pytest.mark.asyncio
async def test_list_messages_returns_seq_ordered_copy() -> None:
    store = InMemoryThreadStore()
    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    msgs = await store.list_messages("s1")

    assert [m.seq for m in msgs] == [1, 2]
    assert [m.content for m in msgs] == ["a", "b"]
    assert await store.list_messages("missing") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'InMemoryThreadStore'`.

- [ ] **Step 3: Write the protocol and in-memory store**

Create `src/ecommerce_agent/threads/store.py`:

```python
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Protocol

from ecommerce_agent.threads.messages import ThreadMessage

if TYPE_CHECKING:
    from ecommerce_agent.sessions.bus import SessionBus

logger = logging.getLogger(__name__)


class ThreadStore(Protocol):
    async def append(self, message: ThreadMessage) -> ThreadMessage:
        """Persist `message`, assigning the next per-session seq. Returns the stored copy."""
        ...

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        """Return all messages for `session_id`, ordered by seq."""
        ...


class InMemoryThreadStore:
    """Async, test-only ThreadStore. Mongo is the prod source of truth (MongoThreadStore)."""

    def __init__(self) -> None:
        self._messages: dict[str, list[ThreadMessage]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        async with self._lock:
            bucket = self._messages[message.session_id]
            stored = message.model_copy(update={"seq": len(bucket) + 1})
            bucket.append(stored)
            return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        async with self._lock:
            return list(self._messages.get(session_id, ()))


async def append_and_publish(
    store: ThreadStore,
    bus: SessionBus,
    message: ThreadMessage,
) -> ThreadMessage:
    """Persist first (source of truth), then best-effort publish a thread.append event.

    A publish failure never fails the append; reload (`list_messages`) is authoritative.
    """
    stored = await store.append(message)
    try:
        bus.publish(stored.session_id, {"event": "thread.append", "message": stored.model_dump()})
    except Exception:  # pragma: no cover - defensive; bus is in-process
        logger.warning("thread.append publish failed for session %s", stored.session_id, exc_info=True)
    return stored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/threads/store.py tests/test_thread_store.py
git commit -m "feat(m2): add ThreadStore protocol and InMemoryThreadStore"
```

---

### Task 4: `SessionBus` + `Subscription`

**Files:**
- Create: `src/ecommerce_agent/sessions/__init__.py`
- Create: `src/ecommerce_agent/sessions/bus.py`
- Test: `tests/test_session_bus.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_bus.py`:

```python
import asyncio

import pytest

from ecommerce_agent.sessions.bus import SessionBus


@pytest.mark.asyncio
async def test_subscribe_buffers_events_published_after_open() -> None:
    bus = SessionBus()

    async with bus.subscription("s1") as sub:
        # published while subscribed -> buffered in this subscriber's queue
        bus.publish("s1", {"event": "token", "text": "hi"})
        bus.publish("s1", {"event": "done"})

        first = await asyncio.wait_for(sub.queue.get(), timeout=1)
        second = await asyncio.wait_for(sub.queue.get(), timeout=1)

    assert first == {"event": "token", "text": "hi"}
    assert second == {"event": "done"}


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers_and_cleans_up() -> None:
    bus = SessionBus()

    async with bus.subscription("s1") as a, bus.subscription("s1") as b:
        bus.publish("s1", {"event": "x"})
        assert (await asyncio.wait_for(a.queue.get(), timeout=1)) == {"event": "x"}
        assert (await asyncio.wait_for(b.queue.get(), timeout=1)) == {"event": "x"}

    # both unsubscribed -> publishing is a no-op, session dropped from the registry
    bus.publish("s1", {"event": "y"})
    assert bus.subscriber_count("s1") == 0


@pytest.mark.asyncio
async def test_publish_to_session_without_subscribers_is_noop() -> None:
    bus = SessionBus()
    bus.publish("nobody", {"event": "x"})  # must not raise
    assert bus.subscriber_count("nobody") == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.sessions'`.

- [ ] **Step 3: Write the bus**

Create `src/ecommerce_agent/sessions/__init__.py` (empty file).

Create `src/ecommerce_agent/sessions/bus.py`:

```python
from __future__ import annotations

import asyncio
from types import TracebackType


class Subscription:
    """A single live subscriber. The SSE endpoint drains `queue`."""

    def __init__(self, bus: SessionBus, session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue()

    async def __aenter__(self) -> Subscription:
        self._bus._add(self._session_id, self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bus._remove(self._session_id, self)


class SessionBus:
    """In-process per-session pub/sub. Single-instance only (multi-instance is M4)."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = {}

    def subscription(self, session_id: str) -> Subscription:
        return Subscription(self, session_id)

    def publish(self, session_id: str, event: dict) -> None:
        for sub in list(self._subscribers.get(session_id, ())):
            sub.queue.put_nowait(event)

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, ()))

    def _add(self, session_id: str, sub: Subscription) -> None:
        self._subscribers.setdefault(session_id, set()).add(sub)

    def _remove(self, session_id: str, sub: Subscription) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        subs.discard(sub)
        if not subs:
            self._subscribers.pop(session_id, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_bus.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/__init__.py src/ecommerce_agent/sessions/bus.py tests/test_session_bus.py
git commit -m "feat(m2): add in-process SessionBus pub/sub"
```

---

### Task 5: Per-session MCP connection headers

**Files:**
- Modify: `src/ecommerce_agent/mcp_client.py:40-83`
- Test: `tests/test_mcp_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_client.py`:

```python
def test_spring_headers_override_user_and_session() -> None:
    from ecommerce_agent.config import Settings
    from ecommerce_agent.mcp_client import spring_headers

    settings = Settings(_env_file=None, spring_mcp_service_token="tok")

    headers = spring_headers(settings, user_id="7", session_id="sess-abc")

    assert headers["X-Service-Token"] == "tok"
    assert headers["X-User-Id"] == "7"
    assert headers["X-Session-Id"] == "sess-abc"


def test_build_mcp_connections_uses_session_headers() -> None:
    from ecommerce_agent.config import Settings
    from ecommerce_agent.mcp_client import build_mcp_connections

    settings = Settings(_env_file=None)

    connections = build_mcp_connections(settings, user_id="7", session_id="sess-abc")

    assert connections["spring"]["headers"]["X-Session-Id"] == "sess-abc"
    assert connections["spring"]["headers"]["X-User-Id"] == "7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_client.py::test_spring_headers_override_user_and_session -v`
Expected: FAIL — `TypeError: spring_headers() got an unexpected keyword argument 'user_id'`.

- [ ] **Step 3: Thread session identity through the connection builders**

In `src/ecommerce_agent/mcp_client.py`, replace `spring_headers`, `build_mcp_connections`, and `build_mcp_client`:

```python
def spring_headers(
    settings: Settings,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    return {
        "X-Service-Token": settings.spring_mcp_service_token,
        "X-User-Id": user_id or settings.spring_mcp_user_id,
        "X-Session-Id": session_id or settings.spring_mcp_session_id,
    }


def build_mcp_connections(
    settings: Settings | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    settings = settings or get_settings()
    timeout = timedelta(seconds=settings.mcp_request_timeout_seconds)
    sse_read_timeout = timedelta(seconds=settings.mcp_sse_read_timeout_seconds)

    connections: dict[str, dict[str, Any]] = {
        SPRING_SERVER_NAME: {
            "transport": "streamable_http",
            "url": settings.spring_mcp_url,
            "headers": spring_headers(settings, user_id=user_id, session_id=session_id),
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }
    }

    if settings.modelscope_mcp_url:
        connections[MODELSCOPE_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": settings.modelscope_mcp_url,
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }

    if settings.python_mcp_url:
        connections[PYTHON_SERVER_NAME] = {
            "transport": "streamable_http",
            "url": settings.python_mcp_url,
            "timeout": timeout,
            "sse_read_timeout": sse_read_timeout,
        }

    return connections


def build_mcp_client(
    settings: Settings | None = None,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
) -> MultiServerMCPClient:
    return MultiServerMCPClient(
        build_mcp_connections(settings, user_id=user_id, session_id=session_id)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_client.py -v`
Expected: PASS (existing tests still pass — the new kwargs default to the settings values).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/mcp_client.py tests/test_mcp_client.py
git commit -m "feat(m2): allow per-session X-User-Id/X-Session-Id MCP headers"
```

---

### Task 6: `SessionRegistry` + per-session runtime + idle reaper

**Files:**
- Create: `src/ecommerce_agent/sessions/registry.py`
- Test: `tests/test_session_registry.py`

The registry builds a per-session runtime via an injected async `build_runtime(session_id)` factory (so tests don't need Docker or MCP). The default factory is wired in Task 8.

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_registry.py`:

```python
import pytest

from ecommerce_agent.sessions.registry import SessionRegistry, SessionRuntime


def make_runtime(session_id: str, sandbox: object) -> SessionRuntime:
    return SessionRuntime(
        session_id=session_id,
        agent=object(),
        mcp_client=object(),
        sandbox=sandbox,
    )


@pytest.mark.asyncio
async def test_create_then_get_returns_same_runtime() -> None:
    built: list[str] = []

    async def build(session_id: str) -> SessionRuntime:
        built.append(session_id)
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    session_id = await registry.create()
    runtime = await registry.get(session_id)

    assert runtime.session_id == session_id
    assert built == [session_id]  # built once, then cached
    assert await registry.get(session_id) is runtime


@pytest.mark.asyncio
async def test_get_unknown_session_raises_keyerror() -> None:
    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=object())

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=1800, max_live_sessions=50)

    with pytest.raises(KeyError):
        await registry.get("nope")


@pytest.mark.asyncio
async def test_reap_idle_closes_sandbox_and_drops_session() -> None:
    closed: list[str] = []

    class FakeSandbox:
        def __init__(self, session_id: str) -> None:
            self._session_id = session_id

        def close(self) -> None:
            closed.append(self._session_id)

    async def build(session_id: str) -> SessionRuntime:
        return make_runtime(session_id, sandbox=FakeSandbox(session_id))

    registry = SessionRegistry(build_runtime=build, idle_ttl_seconds=0, max_live_sessions=50)
    session_id = await registry.create()

    reaped = await registry.reap_idle()

    assert reaped == [session_id]
    assert closed == [session_id]
    with pytest.raises(KeyError):
        await registry.get(session_id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'SessionRegistry'`.

- [ ] **Step 3: Write the registry**

Create `src/ecommerce_agent/sessions/registry.py`:

```python
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionRuntime:
    session_id: str
    agent: Any
    mcp_client: Any
    sandbox: Any
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    def close(self) -> None:
        close = getattr(self.sandbox, "close", None)
        if callable(close):
            close()


BuildRuntime = Callable[[str], Awaitable[SessionRuntime]]


class SessionRegistry:
    """Owns per-session runtimes; builds lazily on create, reaps idle ones.

    Single-instance/in-process only (multi-instance is M4).
    """

    def __init__(
        self,
        *,
        build_runtime: BuildRuntime,
        idle_ttl_seconds: int,
        max_live_sessions: int,
    ) -> None:
        self._build_runtime = build_runtime
        self._idle_ttl_seconds = idle_ttl_seconds
        self._max_live_sessions = max_live_sessions
        self._runtimes: dict[str, SessionRuntime] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> str:
        session_id = uuid.uuid4().hex
        # Make room BEFORE building the (expensive, Docker-backed) runtime, so we never
        # build a container only to discard it under the cap.
        async with self._lock:
            self._make_room_locked()
        runtime = await self._build_runtime(session_id)
        try:
            async with self._lock:
                # A concurrent create may have refilled the map during the build; evict
                # again so this session fits without breaching the cap.
                self._make_room_locked()
                self._runtimes[session_id] = runtime
        except Exception:
            runtime.close()
            raise
        return session_id

    async def get(self, session_id: str) -> SessionRuntime:
        async with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                raise KeyError(session_id)
            runtime.touch()
            return runtime

    async def reap_idle(self) -> list[str]:
        async with self._lock:
            return self._reap_idle_locked()

    async def close_all(self) -> None:
        async with self._lock:
            for runtime in self._runtimes.values():
                runtime.close()
            self._runtimes.clear()

    def _reap_idle_locked(self) -> list[str]:
        reaped: list[str] = []
        for session_id, runtime in list(self._runtimes.items()):
            if runtime.idle_seconds() >= self._idle_ttl_seconds:
                runtime.close()
                del self._runtimes[session_id]
                reaped.append(session_id)
        return reaped

    def _make_room_locked(self) -> list[str]:
        """Reap idle runtimes; if still at/over the cap, evict the oldest until under it."""
        reaped = self._reap_idle_locked()
        while len(self._runtimes) >= self._max_live_sessions:
            oldest = min(self._runtimes.values(), key=lambda r: r.last_used)
            oldest.close()
            del self._runtimes[oldest.session_id]
            reaped.append(oldest.session_id)
        return reaped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/registry.py tests/test_session_registry.py
git commit -m "feat(m2): add SessionRegistry with per-session runtimes and idle reaper"
```

---

### Task 7: Turn runner (`run_turn`)

**Files:**
- Create: `src/ecommerce_agent/sessions/turn.py`
- Test: `tests/test_session_turn.py`

`run_turn` consumes the agent's `astream_events`, publishes ephemeral `token`/`tool` frames, appends a durable `agent_answer`, and publishes a `done` boundary marker. It reuses the existing trace mapping.

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_turn.py`:

```python
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.store import InMemoryThreadStore


class FakeAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str) -> AsyncIterator[dict]:
        assert inputs["messages"][0]["content"] == "hello"
        assert version == "v2"
        yield {"event": "on_tool_start", "name": "inventory_query", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Inventory looks healthy.")},
        }
        yield {"event": "on_tool_end", "name": "inventory_query", "data": {}}


@pytest.mark.asyncio
async def test_run_turn_publishes_events_and_appends_answer() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()
    seen: list[dict] = []

    async with bus.subscription("s1") as sub:
        await run_turn(
            agent=FakeAgent(),
            message="hello",
            session_id="s1",
            turn_id="t1",
            store=store,
            bus=bus,
            recursion_limit=80,
        )
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())

    kinds = [e["event"] for e in seen]
    assert "tool" in kinds
    assert "token" in kinds
    assert "thread.append" in kinds
    assert kinds[-1] == "done"

    messages = await store.list_messages("s1")
    assert [m.type for m in messages] == ["agent_answer"]
    assert messages[0].content == "Inventory looks healthy."
    assert messages[0].turn_id == "t1"
    assert messages[0].actor_id == "agent"


@pytest.mark.asyncio
async def test_run_turn_failure_appends_durable_agent_answer() -> None:
    class ExplodingAgent:
        async def astream_events(self, inputs: dict, config: dict, version: str) -> AsyncIterator[dict]:
            raise RuntimeError("boom")
            yield  # pragma: no cover

    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ExplodingAgent(),
        message="hi",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
    )

    # A late reload must show a durable failure response, not an orphan user turn.
    messages = await store.list_messages("s1")
    assert [m.type for m in messages] == ["agent_answer"]
    assert messages[0].status == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_turn.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.sessions.turn'`.

- [ ] **Step 3: Write the turn runner**

Create `src/ecommerce_agent/sessions/turn.py`:

```python
from __future__ import annotations

import logging
from typing import Any

from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import ThreadStore, append_and_publish
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

logger = logging.getLogger(__name__)


def _trace_event_to_frame(event: TraceEvent) -> dict | None:
    if event.event_type == "answer_chunk":
        return {"event": "token", "text": event.result_summary or ""}
    if event.event_type == "tool_call":
        return {"event": "tool", "name": event.name, "phase": event.phase}
    return None


async def run_turn(
    *,
    agent: Any,
    message: str,
    session_id: str,
    turn_id: str,
    store: ThreadStore,
    bus: SessionBus,
    recursion_limit: int,
) -> TraceRecord:
    """Run one agent turn as a server task: stream live frames, append the answer, mark done."""
    record = TraceRecord(session_id=session_id, turn_id=turn_id)
    inputs = {"messages": [{"role": "user", "content": message}]}
    config = {"recursion_limit": recursion_limit}
    raw_events = agent.astream_events(inputs, config=config, version="v2")
    try:
        async for event in capture(raw_events, record):
            frame = _trace_event_to_frame(event)
            if frame is not None:
                bus.publish(session_id, frame)
        await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="agent_answer",
                content=record.answer,
                turn_id=turn_id,
                trace_id=record.trace_id,
                actor_id="agent",
            ),
        )
    except Exception:
        logger.exception("agent turn failed for session %s", session_id)
        # Durable failure response: a late reload must show a reply, not an orphan user turn.
        await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="agent_answer",
                content="Sorry, I could not complete that request. Please try again.",
                turn_id=turn_id,
                trace_id=record.trace_id,
                actor_id="agent",
                status="failed",
            ),
        )
        bus.publish(session_id, {"event": "error", "message": "Unable to complete the turn."})
    finally:
        if record.ended_at is None:
            record.finish()
        bus.publish(session_id, {"event": "done", "turn_id": turn_id})
    return record
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_turn.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/turn.py tests/test_session_turn.py
git commit -m "feat(m2): add per-session agent turn runner"
```

---

### Task 8: Default runtime factory (per-session agent + sandbox + MCP client)

**Files:**
- Create: `src/ecommerce_agent/sessions/factory.py`
- Test: `tests/test_session_factory.py`

This builds a real `SessionRuntime` for production: a per-session `DockerSandbox`, a per-session MCP client carrying the session headers, the loaded read tools + viz tools, and the analyst agent (the coordinator replaces the analyst in the Phase 2 plan).

- [ ] **Step 1: Write the failing test**

Create `tests/test_session_factory.py`:

```python
import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions import factory as factory_module
from ecommerce_agent.sessions.factory import build_session_runtime


@pytest.mark.asyncio
async def test_build_session_runtime_wires_session_scoped_pieces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    def fake_build_mcp_client(settings, *, user_id, session_id):
        captured["user_id"] = user_id
        captured["session_id"] = session_id
        return object()

    async def fake_load_spring_read_tools(client):
        return [FakeTool("order_query")]

    def fake_build_sandbox(settings, *, session_id):
        captured["sandbox_session_id"] = session_id
        return object()

    def fake_build_sales_analyst(model, *, spring_read_tools, viz_tools, backend):
        captured["tools"] = [t.name for t in spring_read_tools]
        return "ANALYST"

    monkeypatch.setattr(factory_module, "build_mcp_client", fake_build_mcp_client)
    monkeypatch.setattr(factory_module, "load_spring_read_tools", fake_load_spring_read_tools)
    monkeypatch.setattr(factory_module, "build_session_sandbox", fake_build_sandbox)
    monkeypatch.setattr(factory_module, "get_primary_model", lambda settings: object())
    monkeypatch.setattr(factory_module, "build_sales_analyst", fake_build_sales_analyst)

    settings = Settings(_env_file=None, llm_api_key="k", spring_mcp_user_id="9")

    runtime = await build_session_runtime("sess-1", settings)

    assert runtime.session_id == "sess-1"
    assert runtime.agent == "ANALYST"
    assert captured["session_id"] == "sess-1"
    assert captured["user_id"] == "9"
    assert captured["sandbox_session_id"] == "sess-1"
    assert captured["tools"] == ["order_query"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.sessions.factory'`.

- [ ] **Step 3: Write the factory**

Create `src/ecommerce_agent/sessions/factory.py`:

```python
from __future__ import annotations

import logging

from ecommerce_agent.agents import build_sales_analyst
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import (
    build_mcp_client,
    load_modelscope_viz_tools,
    load_spring_read_tools,
)
from ecommerce_agent.models import get_primary_model
from ecommerce_agent.sandbox import DockerSandbox
from ecommerce_agent.sandbox.config import limits_from_settings
from ecommerce_agent.sessions.registry import SessionRuntime

logger = logging.getLogger(__name__)


def build_session_sandbox(settings: Settings, *, session_id: str) -> DockerSandbox:
    return DockerSandbox(limits_from_settings(settings), session_id=session_id)


async def build_session_runtime(session_id: str, settings: Settings) -> SessionRuntime:
    """Build a per-session runtime: session-scoped MCP headers, sandbox, and agent."""
    mcp_client = build_mcp_client(
        settings,
        user_id=settings.spring_mcp_user_id,
        session_id=session_id,
    )
    spring_tools = await load_spring_read_tools(mcp_client)
    if settings.modelscope_mcp_url:
        try:
            viz_tools = await load_modelscope_viz_tools(mcp_client)
        except Exception:
            logger.warning("ModelScope MCP unavailable; continuing without viz tools", exc_info=True)
            viz_tools = []
    else:
        viz_tools = []

    sandbox = build_session_sandbox(settings, session_id=session_id)
    model = get_primary_model(settings)
    agent = build_sales_analyst(
        model,
        spring_read_tools=spring_tools,
        viz_tools=viz_tools,
        backend=sandbox,
    )
    return SessionRuntime(
        session_id=session_id,
        agent=agent,
        mcp_client=mcp_client,
        sandbox=sandbox,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session_factory.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/factory.py tests/test_session_factory.py
git commit -m "feat(m2): add default per-session runtime factory"
```

---

### Task 9: Sessions API router (create / messages / thread / stream)

**Files:**
- Create: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

The router reads `store`, `bus`, `registry`, and `settings` from `request.app.state` (wired in Task 11). `POST /messages` appends the user message, spawns `run_turn` as a background task, and returns `202`. `GET /stream` subscribes-first-then-replays.

- [ ] **Step 1: Write the failing test**

Create `tests/test_sessions_api.py`:

```python
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.registry import SessionRegistry, SessionRuntime
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

    async def build_runtime(session_id: str) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id, agent=FakeAgent(), mcp_client=object(), sandbox=object()
        )

    app.state.session_registry = SessionRegistry(
        build_runtime=build_runtime, idle_ttl_seconds=1800, max_live_sessions=50
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

        # Subscribe to the stream, then post a message so we observe live frames.
        with client.stream("GET", f"/api/sessions/{session_id}/stream") as stream:
            post = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello"})
            assert post.status_code == 202
            body = ""
            for chunk in stream.iter_text():
                body += chunk
                if "event: done" in body:
                    break

    assert "event: token" in body
    assert "Hi there." in body
    assert "event: done" in body

    thread = client.get(f"/api/sessions/{session_id}/thread").json()
    types = [m["type"] for m in thread["messages"]]
    assert types == ["user", "agent_answer"]
    assert thread["messages"][0]["seq"] == 1
    assert thread["messages"][1]["seq"] == 2


def test_stream_replays_backlog_for_late_subscriber() -> None:
    with TestClient(build_test_app()) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        client.post(f"/api/sessions/{session_id}/messages", json={"message": "hello"})

        # New subscriber connects after the turn finished; backlog must replay.
        with client.stream("GET", f"/api/sessions/{session_id}/stream") as stream:
            body = ""
            for chunk in stream.iter_text():
                body += chunk
                if "agent_answer" in body:
                    break

    frames = [line for line in body.splitlines() if line.startswith("data:")]
    assert any('"type": "agent_answer"' in f for f in frames)


def test_message_to_unknown_session_returns_404() -> None:
    with TestClient(build_test_app()) as client:
        response = client.post("/api/sessions/nope/messages", json={"message": "hi"})
        assert response.status_code == 404


def _decode_sse(body: str) -> list[dict]:
    return [json.loads(line[len("data:"):].strip()) for line in body.splitlines() if line.startswith("data:")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.api.sessions'`.

- [ ] **Step 3: Write the router**

Create `src/ecommerce_agent/api/sessions.py`:

```python
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StringConstraints
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import append_and_publish

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


class MessageRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> dict[str, str]:
    session_id = await request.app.state.session_registry.create()
    return {"session_id": session_id}


@router.get("/{session_id}/thread")
async def get_thread(session_id: str, request: Request) -> dict[str, Any]:
    messages = await request.app.state.thread_store.list_messages(session_id)
    return {"session_id": session_id, "messages": [m.model_dump() for m in messages]}


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(session_id: str, payload: MessageRequest, request: Request) -> dict[str, Any]:
    registry = request.app.state.session_registry
    try:
        runtime = await registry.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc

    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    settings = request.app.state.settings
    turn_id = uuid.uuid4().hex

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

    task = asyncio.create_task(
        run_turn(
            agent=runtime.agent,
            message=payload.message,
            session_id=session_id,
            turn_id=turn_id,
            store=store,
            bus=bus,
            recursion_limit=settings.agent_recursion_limit,
        )
    )
    _background_tasks = request.app.state.background_tasks
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {"turn_id": turn_id, "user_message_id": user_message.message_id}


@router.get("/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    store = request.app.state.thread_store
    bus = request.app.state.session_bus

    async def events() -> AsyncIterator[dict[str, str]]:
        async with bus.subscription(session_id) as sub:
            # Subscribe-first-then-replay: backlog up to the current seq, then live with a seq cursor.
            backlog = await store.list_messages(session_id)
            cursor = backlog[-1].seq if backlog else 0
            for message in backlog:
                yield {"event": "thread.append", "data": _data({"message": message.model_dump()})}
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                if event["event"] == "thread.append" and event["message"]["seq"] <= cursor:
                    continue  # already sent in the backlog
                yield {"event": event["event"], "data": _data(event)}

    return EventSourceResponse(events())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_sessions_api.py -v`
Expected: PASS. (The `background_tasks` set is created per-app in Task 11; the test app needs it — add `app.state.background_tasks = set()` to `build_test_app`.)

> Note for the implementer: add `app.state.background_tasks = set()` to the `build_test_app` helper in the test, mirroring Task 11's app wiring.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m2): add sessions API with unified SSE stream and reload"
```

---

### Task 10: `MongoThreadStore` + gated integration test

**Files:**
- Create: `src/ecommerce_agent/threads/mongo.py`
- Test: `tests/test_mongo_thread_store.py`, `tests/integration/test_mongo_thread_store.py`

This lands the production ThreadStore **before** the app wiring (Task 11) imports it, so no intermediate commit references a missing module.

- [ ] **Step 1: Write the failing unit test (Mongo store, fake motor collections)**

Create `tests/test_mongo_thread_store.py`:

```python
import pytest

from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.mongo import MongoThreadStore


class FakeCounters:
    def __init__(self) -> None:
        self._seqs: dict[str, int] = {}

    async def find_one_and_update(self, filt, update, upsert, return_document):
        sid = filt["_id"]
        self._seqs[sid] = self._seqs.get(sid, 0) + 1
        return {"_id": sid, "seq": self._seqs[sid]}


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d[key])
        return self

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d

        return gen()


class FakeMessages:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc):
        self.docs.append(doc)

    def find(self, filt):
        return FakeCursor([dict(d) for d in self.docs if d["session_id"] == filt["session_id"]])


@pytest.mark.asyncio
async def test_mongo_store_append_and_list() -> None:
    store = MongoThreadStore(messages=FakeMessages(), counters=FakeCounters())

    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    msgs = await store.list_messages("s1")
    assert [m.seq for m in msgs] == [1, 2]
    assert [m.content for m in msgs] == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mongo_thread_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.threads.mongo'`.

- [ ] **Step 3: Write `MongoThreadStore`**

Create `src/ecommerce_agent/threads/mongo.py`:

```python
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


class MongoThreadStore:
    """Source-of-truth ThreadStore backed by MongoDB via motor.

    Per-session monotonic `seq` comes from an atomic counter document.
    """

    def __init__(self, *, messages: Any, counters: Any) -> None:
        self._messages = messages
        self._counters = counters

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoThreadStore:
        db = AsyncIOMotorClient(settings.mongo_url)[settings.mongo_db]
        return cls(messages=db["thread_messages"], counters=db["thread_counters"])

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        counter = await self._counters.find_one_and_update(
            {"_id": message.session_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        stored = message.model_copy(update={"seq": counter["seq"]})
        await self._messages.insert_one(stored.model_dump())
        return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        cursor = self._messages.find({"session_id": session_id}).sort("seq", 1)
        return [ThreadMessage(**{k: v for k, v in doc.items() if k != "_id"}) async for doc in cursor]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mongo_thread_store.py -v`
Expected: PASS.

- [ ] **Step 5: Write the gated real-Mongo integration test**

Create `tests/integration/test_mongo_thread_store.py`:

```python
import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.mongo import MongoThreadStore

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_real_mongo_append_and_reload() -> None:
    if not os.environ.get("RUN_MONGO_INTEGRATION"):
        pytest.skip("set RUN_MONGO_INTEGRATION and run a local Mongo to exercise this")

    settings = Settings(_env_file=None)
    store = MongoThreadStore.from_settings(settings)
    session_id = f"itest-{os.getpid()}"

    await store.append(ThreadMessage(session_id=session_id, type="user", content="hi"))
    msgs = await store.list_messages(session_id)

    assert msgs[-1].content == "hi"
    assert msgs[-1].seq >= 1
```

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/threads/mongo.py tests/test_mongo_thread_store.py tests/integration/test_mongo_thread_store.py
git commit -m "feat(m2): add MongoThreadStore with per-session seq counter"
```

---

### Task 11: Wire sessions/bus/registry into the app (chat path still mounted)

**Files:**
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_app.py`

This adds the session subsystem **alongside** the existing `/api/chat/stream` so the repo stays green; Task 12 retires the chat path after its consumers are migrated. Crucially, the existing global `app.state.mcp_client` is **kept** — `/health/mcp` continues to probe through it (a default-identity probe client, distinct from the per-session MCP clients that live inside each runtime).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_session_lifecycle_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    import ecommerce_agent.api.app as app_module
    from ecommerce_agent.sessions.registry import SessionRuntime

    async def fake_build_runtime(session_id: str) -> SessionRuntime:
        return SessionRuntime(
            session_id=session_id, agent=FakeAgent(), mcp_client=object(), sandbox=object()
        )

    monkeypatch.setattr(app_module, "make_runtime_builder", lambda settings: fake_build_runtime)
    monkeypatch.setattr(
        app_module, "build_sandbox_backend", lambda settings: SimpleNamespace(close=lambda: None)
    )

    app = create_app(settings=make_settings())
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        assert client.post(
            f"/api/sessions/{session_id}/messages", json={"message": "hello"}
        ).status_code == 202
        thread = client.get(f"/api/sessions/{session_id}/thread").json()
        assert [m["type"] for m in thread["messages"]] == ["user", "agent_answer"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_session_lifecycle_end_to_end -v`
Expected: FAIL — `AttributeError: module 'ecommerce_agent.api.app' has no attribute 'make_runtime_builder'`.

- [ ] **Step 3: Extend `app.py` (additive — keep the chat path and the health probe client)**

In `src/ecommerce_agent/api/app.py`:

1. **Add** the new imports (keep the existing `chat_router` import):

```python
from ecommerce_agent.api.sessions import router as sessions_router
from ecommerce_agent.sessions.factory import build_session_runtime
from ecommerce_agent.sessions.registry import SessionRegistry
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.threads.mongo import MongoThreadStore
```

2. Add the runtime-builder factory (a seam tests monkeypatch):

```python
def make_runtime_builder(settings: Settings):
    async def build_runtime(session_id: str):
        return await build_session_runtime(session_id, settings)

    return build_runtime
```

3. In `lifespan`, **keep** the existing `app.state.mcp_client` and `app.state.sandbox_backend`
   initialization (the chat path and `/health/mcp` need them) and **add** the session subsystem +
   reaper:

```python
    app.state.thread_store = getattr(app.state, "thread_store", None) or MongoThreadStore.from_settings(settings)
    app.state.session_bus = getattr(app.state, "session_bus", None) or SessionBus()
    app.state.background_tasks = getattr(app.state, "background_tasks", None) or set()
    app.state.session_registry = getattr(app.state, "session_registry", None) or SessionRegistry(
        build_runtime=make_runtime_builder(settings),
        idle_ttl_seconds=settings.session_idle_ttl_seconds,
        max_live_sessions=settings.max_live_sessions,
    )
    app.state.reaper_task = asyncio.create_task(_reap_loop(app))
```

In the `finally` block, alongside the existing sandbox close, add:

```python
        app.state.reaper_task.cancel()
        await app.state.session_registry.close_all()
```

And add the helper at module scope:

```python
async def _reap_loop(app: FastAPI) -> None:
    registry = app.state.session_registry
    try:
        while True:
            await asyncio.sleep(60)
            await registry.reap_idle()
    except asyncio.CancelledError:
        pass
```

4. In `create_app`, initialize the new state to `None` (so tests can inject) and mount **both**
   routers. Keep the existing chat router line for now:

```python
    app.state.thread_store = None
    app.state.session_bus = None
    app.state.session_registry = None
    app.state.background_tasks = None
    ...
    app.include_router(chat_router)      # retired in Task 12
    app.include_router(sessions_router)
```

5. Leave `/health` and `/health/mcp` unchanged in this task — `/health/mcp` keeps probing
   `app.state.mcp_client`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS — the new session test passes and the existing chat/health tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "feat(m2): mount sessions/bus/registry alongside the chat path"
```

---

### Task 12: Retire `/api/chat/stream` (migrate consumers first, then delete)

**Files:**
- Modify: `tests/integration/test_chat_stream_live.py`, `tests/integration/test_hero_live_smoke.py`, `src/ecommerce_agent/evals/live_reliability.py`
- Modify: `tests/test_app.py` (remove obsolete chat/shared-backend tests; drop the now-defunct `build_sandbox_backend` monkeypatch in `test_session_lifecycle_end_to_end`)
- Modify: `src/ecommerce_agent/api/app.py` (unmount chat router; drop the shared sandbox backend)
- Delete: `src/ecommerce_agent/api/chat.py`

Order matters: migrate consumers **before** deleting `chat.py`, so no commit leaves a broken suite.

- [ ] **Step 1: Migrate the live/eval consumers to the session flow**

In `tests/integration/test_chat_stream_live.py`, `tests/integration/test_hero_live_smoke.py`, and
`src/ecommerce_agent/evals/live_reliability.py`, replace the single
`POST /api/chat/stream {"message": ...}` call with the two-step session flow:
`POST /api/sessions` → take `session_id` → open `GET /api/sessions/{id}/stream` →
`POST /api/sessions/{id}/messages {"message": ...}` → read frames until `event: done`.
(These are `live`/`integration` gated and do not run in the default suite.)

- [ ] **Step 2: Remove the obsolete unit tests**

In `tests/test_app.py`, delete the tests that POST to `/api/chat/stream` and the shared-backend
lifespan test (per-session sandboxes via the registry replace the shared backend):
`test_chat_stream_maps_agent_events_to_sse_frames`, `test_chat_stream_rejects_blank_message`,
`test_chat_stream_error_message_does_not_leak_internal_exception`,
`test_chat_stream_lazily_builds_analyst_with_backend`,
`test_chat_stream_falls_back_when_modelscope_is_unavailable`,
`test_health_reports_unknown_tool_count_for_injected_agent`, and
`test_lifespan_builds_and_closes_sandbox_backend`. Their behavior is now covered by
`tests/test_session_turn.py`, `tests/test_sessions_api.py`, `tests/test_session_registry.py`, and
`test_session_lifecycle_end_to_end`. In `test_session_lifecycle_end_to_end`, remove the
`monkeypatch.setattr(app_module, "build_sandbox_backend", ...)` line (that function is deleted in
Step 3). Keep the `/health` and `/health/mcp` tests.

- [ ] **Step 3: Delete `chat.py`; drop the chat path and shared sandbox from `app.py`**

```bash
git rm src/ecommerce_agent/api/chat.py
```

In `src/ecommerce_agent/api/app.py`: remove the `chat_router` import and its
`app.include_router(chat_router)`; remove `build_sandbox_backend`, the
`app.state.sandbox_backend` init, and its `close()` in `finally` (per-session sandboxes are built by
the factory and closed by `SessionRegistry.close_all`). Set `/health`'s `agent_ready` to
`app.state.session_registry is not None` and drop the `tool_count` field (per-agent tool counts move
to `/health/mcp` in Phase 3).

- [ ] **Step 4: Run the full default suite**

Run: `uv run pytest -q`
Expected: PASS (integration/live/docker tests skip without their env). Then `uv run ruff check .` →
no errors.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(m2): retire /api/chat/stream after migrating tests and evals"
```

---

## Self-Review

**Spec coverage (spec §3, §4, §10 step 2):**
- §3.1 endpoints (`POST /api/sessions`, `GET …/thread`, `POST …/messages`, `GET …/stream`) → Task 9; mounted in Task 11; `/api/chat/stream` retired → Task 12.
- §3.2 ThreadStore async protocol + InMemoryThreadStore + MongoThreadStore + `seq` + best-effort publish → Tasks 2, 3, 10.
- §3.2 message schema with `seq`/`turn_id`/`trace_id`/`actor_id`/`execution_id` → Task 2.
- §3.3 per-session agent + session MCP headers + per-session DockerSandbox + idle reaper/cap → Tasks 5, 6, 8, 11.
- §3.4 per-session trace (`TraceRecord(session_id, turn_id)`) → Task 7.
- §4 unified stream (token/tool ephemeral, thread.append durable, done marker) + subscribe-first-then-replay with seq cursor → Tasks 4, 7, 9.
- §10 migrate M1 live/eval tests → Task 12 step 1.

**Build-order safety (review fixes):** MongoThreadStore (Task 10) lands before the app wiring that imports it (Task 11); the chat path and its consumers are retired only after migration (Task 12), so every commit is green. `/health/mcp` keeps its global probe client (Task 11). The registry reaps/evicts *before* building a runtime (Task 6 `create`), and a failed turn appends a durable `agent_answer` (Task 7).

**Out of Phase 1 (other plans):** order-manager + coordinator (step 3), approval orchestration + result re-entry (step 4), gated end-to-end approval loop (step 5). Phase 1 deliberately validates the foundation with the existing analyst.

**Placeholder scan:** no TBD/TODO; every code step shows the code; commands have expected output.

**Type consistency:** `ThreadMessage` fields used identically in Tasks 2/3/7/9/10; `SessionRuntime(session_id, agent, mcp_client, sandbox)` consistent in Tasks 6/8/9/11; `bus.subscription(session_id).queue` consistent in Tasks 4/7/9; `append_and_publish(store, bus, message)` consistent in Tasks 3/7/9/(and Task 7 failure path); `build_mcp_client(..., user_id=, session_id=)` consistent in Tasks 5/8.

**Known follow-up for the implementer:** Task 9's test app needs `app.state.background_tasks = set()` (noted inline); Task 11 supplies it for the real app.
