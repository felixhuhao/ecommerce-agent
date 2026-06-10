# M3 Phase 2 — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent's per-turn trace durable and inspectable, and expose session artifacts — add a `TraceStore` (in-memory + Mongo), a timeline projection, trace read + export endpoints, and a session-scoped artifact list endpoint, over the existing M2/M3-Phase-1 session API.

**Architecture:** A new `TraceStore` (async protocol; `InMemoryTraceStore` for tests, `MongoTraceStore` for prod) persists the per-turn `TraceRecord` keyed by `(session_id, turn_id)` — saved best-effort in the existing `run_and_record_trace` background task, so `run_turn` stays Mongo-free for the eval harness. A pure `project_timeline()` collapses the flat event list into ordered spans for the UI (dropping data-URI bytes); the export endpoint returns the full raw record. The artifact endpoint projects from existing thread messages (no new store method). Reads fall back store→in-memory-cache so a just-finished turn is inspectable.

**Tech Stack:** FastAPI, motor (Mongo), pydantic v2 / dataclasses, pytest + pytest-asyncio (`asyncio_mode = "auto"`), `fastapi.testclient.TestClient`.

**Spec:** [docs/2026-06-10-m3-phase2-trace-artifacts-design.md](../2026-06-10-m3-phase2-trace-artifacts-design.md) §3 (trace persistence + endpoints), §4 (artifacts). The React SPA + tabbed rail are a **separate frontend plan**; this one produces the fully tested API the SPA builds on.

**Conventions (from the codebase):** tests live flat in `tests/`, use `Settings(_env_file=None, **overrides)`, `fastapi.testclient.TestClient`, fake stores/collections (see [tests/test_sessions_api.py](../../tests/test_sessions_api.py), [tests/test_mongo_session_store.py](../../tests/test_mongo_session_store.py)). `from __future__ import annotations` atop new modules. Direct endpoint-function calls use `SimpleNamespace(app=app)` as the request (see `test_second_concurrent_send_409_is_side_effect_free`). Run: `uv run pytest -q`; lint: `uv run ruff check .` (line-length 100).

---

### Task 1: `TraceRecord.from_dict` / `TraceEvent.from_dict` (round-trip deserialization)

`record.to_dict()` is `dataclasses.asdict` (one-way: nested `TraceEvent`s become plain dicts). The Mongo store must reconstruct typed records so `project_timeline` (Task 4) sees `TraceEvent` objects, not dicts.

**Files:**
- Modify: `src/ecommerce_agent/trace/schema.py`
- Test: `tests/test_trace_schema.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trace_schema.py`:

```python
def test_trace_record_from_dict_round_trip_rebuilds_events() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(
        TraceEvent(event_type="tool_call", name="order_query", phase="end", duration_ms=12.0)
    )
    record.finish()

    restored = TraceRecord.from_dict(record.to_dict())

    assert isinstance(restored, TraceRecord)
    assert restored.session_id == "s1"
    assert restored.turn_id == "t1"
    assert restored.duration_ms == record.duration_ms
    assert len(restored.events) == 1
    assert isinstance(restored.events[0], TraceEvent)
    assert restored.events[0].name == "order_query"
    assert restored.events[0].duration_ms == 12.0


def test_trace_record_from_dict_ignores_unknown_keys() -> None:
    data = TraceRecord(session_id="s1", turn_id="t1").to_dict()
    data["_id"] = "mongo-oid"
    data["unexpected"] = 1
    data["events"] = []

    restored = TraceRecord.from_dict(data)

    assert restored.session_id == "s1"
    assert not hasattr(restored, "_id")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trace_schema.py -k from_dict -v`
Expected: FAIL — `AttributeError: type object 'TraceRecord' has no attribute 'from_dict'`.

- [ ] **Step 3: Add the classmethods**

In `src/ecommerce_agent/trace/schema.py`, add a `from_dict` classmethod to `TraceEvent` (after `to_dict`):

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceEvent":
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})
```

And to `TraceRecord` (after `to_dict`):

```python
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceRecord":
        names = {f.name for f in dataclasses.fields(cls)}
        kwargs = {
            key: value for key, value in data.items() if key in names and key != "events"
        }
        events = [TraceEvent.from_dict(event) for event in data.get("events", [])]
        return cls(events=events, **kwargs)
```

(`dataclasses` and `Any` are already imported at the top of the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trace_schema.py -v`
Expected: PASS (existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/schema.py tests/test_trace_schema.py
git commit -m "feat(m3): add TraceRecord/TraceEvent.from_dict for round-trip"
```

---

### Task 2: `TraceStore` protocol + `InMemoryTraceStore`

**Files:**
- Create: `src/ecommerce_agent/trace/store.py`
- Test: `tests/test_trace_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trace_store.py`:

```python
import pytest

from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from ecommerce_agent.trace.store import InMemoryTraceStore


@pytest.mark.asyncio
async def test_save_and_get_round_trip() -> None:
    store = InMemoryTraceStore()
    assert await store.get("s1", "t1") is None

    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    await store.save(record)

    got = await store.get("s1", "t1")
    assert got is not None
    assert got.turn_id == "t1"
    assert got.events[0].name == "order_query"


@pytest.mark.asyncio
async def test_resave_same_turn_keeps_one_record() -> None:
    store = InMemoryTraceStore()
    await store.save(TraceRecord(session_id="s1", turn_id="t1", answer="first"))
    # A re-run for the same turn (even with a different trace_id) replaces, never duplicates.
    await store.save(TraceRecord(session_id="s1", turn_id="t1", answer="second"))

    got = await store.get("s1", "t1")
    assert got is not None and got.answer == "second"


@pytest.mark.asyncio
async def test_ping_is_true_for_in_memory() -> None:
    assert await InMemoryTraceStore().ping() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trace_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.trace.store'`.

- [ ] **Step 3: Create the store module**

Create `src/ecommerce_agent/trace/store.py`:

```python
from __future__ import annotations

from typing import Protocol

from ecommerce_agent.trace.schema import TraceRecord


class TraceStore(Protocol):
    async def save(self, record: TraceRecord) -> None:
        """Persist one turn's trace, upserting by (session_id, turn_id)."""
        ...

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        """Return the trace for one turn, or None."""
        ...

    async def ping(self) -> bool:
        """Return whether the backing store is reachable."""
        ...


class InMemoryTraceStore:
    """Async, test-only TraceStore. Mongo is the prod source of truth."""

    def __init__(self) -> None:
        self._records: dict[tuple[str | None, str | None], TraceRecord] = {}

    async def save(self, record: TraceRecord) -> None:
        self._records[(record.session_id, record.turn_id)] = record

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        return self._records.get((session_id, turn_id))

    async def ping(self) -> bool:
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trace_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/store.py tests/test_trace_store.py
git commit -m "feat(m3): add TraceStore protocol + InMemoryTraceStore"
```

---

### Task 3: `MongoTraceStore`

Upserts by the natural read key `(session_id, turn_id)`; a **unique compound index** (created lazily on first `save`, so startup stays network-free like the other Mongo stores) backs it as a safety net. `get` strips Mongo's `_id` and reconstructs a typed `TraceRecord`.

**Files:**
- Create: `src/ecommerce_agent/trace/mongo.py`
- Test: `tests/test_mongo_trace_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mongo_trace_store.py`:

```python
import pytest

from ecommerce_agent.trace.mongo import MongoTraceStore
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


class FakeTraces:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.indexes: list[tuple] = []

    async def create_index(self, keys, unique=False):  # noqa: ANN001
        self.indexes.append((tuple(keys), unique))

    async def update_one(self, filt, update, upsert=False):  # noqa: ANN001
        key = (filt["session_id"], filt["turn_id"])
        self.docs[key] = {"_id": "oid", **update["$set"]}

    async def find_one(self, filt):  # noqa: ANN001
        return self.docs.get((filt["session_id"], filt["turn_id"]))


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_save_upserts_by_turn_and_get_reconstructs_record() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, client=FakeClient())

    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    await store.save(record)
    # Re-run for the same turn with a different trace_id must not create a second doc.
    await store.save(TraceRecord(session_id="s1", turn_id="t1", answer="redo"))

    assert len(traces.docs) == 1
    got = await store.get("s1", "t1")
    assert got is not None
    assert isinstance(got.events[0], TraceEvent)  # _id stripped, events rebuilt as objects
    assert got.answer == "redo"
    assert await store.get("s1", "missing") is None


@pytest.mark.asyncio
async def test_first_save_creates_unique_compound_index_once() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, client=FakeClient())

    await store.save(TraceRecord(session_id="s1", turn_id="t1"))
    await store.save(TraceRecord(session_id="s1", turn_id="t2"))

    assert traces.indexes == [((("session_id", 1), ("turn_id", 1)), True)]


def test_close_closes_client() -> None:
    client = FakeClient()
    MongoTraceStore(traces=FakeTraces(), client=client).close()
    assert client.closed is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mongo_trace_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.trace.mongo'`.

- [ ] **Step 3: Create the Mongo store**

Create `src/ecommerce_agent/trace/mongo.py`:

```python
from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings
from ecommerce_agent.trace.schema import TraceRecord


class MongoTraceStore:
    """Source-of-truth TraceStore backed by MongoDB via motor."""

    def __init__(self, *, traces: Any, client: Any | None = None) -> None:
        self._traces = traces
        self._client = client
        self._indexed = False

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoTraceStore":
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(traces=db["traces"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def _ensure_indexes(self) -> None:
        if self._indexed:
            return
        await self._traces.create_index([("session_id", 1), ("turn_id", 1)], unique=True)
        self._indexed = True

    async def save(self, record: TraceRecord) -> None:
        await self._ensure_indexes()
        await self._traces.update_one(
            {"session_id": record.session_id, "turn_id": record.turn_id},
            {"$set": record.to_dict()},
            upsert=True,
        )

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        doc = await self._traces.find_one({"session_id": session_id, "turn_id": turn_id})
        if doc is None:
            return None
        return TraceRecord.from_dict({key: value for key, value in doc.items() if key != "_id"})

    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mongo_trace_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/mongo.py tests/test_mongo_trace_store.py
git commit -m "feat(m3): add MongoTraceStore (upsert by turn, lazy unique index)"
```

---

### Task 4: `project_timeline` (pure projection)

Collapses the flat `model_call`/`tool_call` start+end events into one merged span each (start carries args/ts; end carries result/duration/tokens/status), ordered by `ts`, summing token totals, surfacing `artifact_id`/`approval_id`, and **dropping** the data-URI `artifact` payload.

**Files:**
- Create: `src/ecommerce_agent/trace/projection.py`
- Test: `tests/test_trace_projection.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trace_projection.py`:

```python
from ecommerce_agent.trace.projection import project_timeline
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def _record_with_spans() -> TraceRecord:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events += [
        TraceEvent(event_type="model_call", name="chat", phase="start",
                   model_call_id="m1", args_summary="prompt", ts=1.0),
        TraceEvent(event_type="model_call", name="chat", phase="end",
                   model_call_id="m1", result_summary="resp", duration_ms=50.0,
                   tokens_in=10, tokens_out=20, ts=1.05),
        TraceEvent(event_type="tool_call", name="generate_line_chart", phase="start",
                   tool_call_id="x1", args_summary="series", ts=2.0),
        TraceEvent(event_type="tool_call", name="generate_line_chart", phase="end",
                   tool_call_id="x1", result_summary="data:image/...", duration_ms=12.0,
                   artifact_id="chart-x1",
                   artifact={"id": "chart-x1", "src": "data:image/svg+xml,<svg/>"}, ts=2.01),
    ]
    record.finish()
    return record


def test_project_timeline_merges_spans_and_drops_artifact_src() -> None:
    timeline = project_timeline(_record_with_spans())

    assert timeline["turn_id"] == "t1"
    assert timeline["span_count"] == 2
    assert timeline["tokens_in_total"] == 10
    assert timeline["tokens_out_total"] == 20

    model, tool = timeline["spans"]
    assert model["kind"] == "model_call"
    assert model["args_summary"] == "prompt"
    assert model["result_summary"] == "resp"
    assert model["duration_ms"] == 50.0

    assert tool["kind"] == "tool_call"
    assert tool["name"] == "generate_line_chart"
    assert tool["args_summary"] == "series"
    assert tool["duration_ms"] == 12.0
    assert tool["artifact_id"] == "chart-x1"
    assert "artifact" not in tool  # data-URI payload dropped
    assert "src" not in tool


def test_project_timeline_orders_by_ts_and_handles_start_only() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events += [
        TraceEvent(event_type="tool_call", name="late", phase="start", tool_call_id="b", ts=5.0),
        TraceEvent(event_type="tool_call", name="early", phase="start", tool_call_id="a", ts=1.0),
    ]

    timeline = project_timeline(record)

    assert [span["name"] for span in timeline["spans"]] == ["early", "late"]
    assert timeline["spans"][0]["duration_ms"] is None  # start with no end
    assert timeline["tokens_in_total"] is None  # no token data anywhere


def test_project_timeline_ignores_answer_chunk_and_unknown_events() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="answer_chunk", result_summary="Hi", ts=1.0))

    timeline = project_timeline(record)

    assert timeline["spans"] == []
    assert timeline["span_count"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_trace_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.trace.projection'`.

- [ ] **Step 3: Create the projection**

Create `src/ecommerce_agent/trace/projection.py`:

```python
from __future__ import annotations

from typing import Any

from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

_SPAN_EVENT_TYPES = {"model_call", "tool_call"}


def _new_span(event: TraceEvent, span_id: str) -> dict[str, Any]:
    return {
        "kind": event.event_type,
        "name": event.name,
        "status": event.status,
        "ts": event.ts,
        "duration_ms": event.duration_ms,
        "args_summary": None,
        "result_summary": None,
        "tokens_in": None,
        "tokens_out": None,
        "span_id": span_id,
        "artifact_id": None,
        "approval_id": None,
        "error_message": None,
    }


def _merge(span: dict[str, Any], event: TraceEvent) -> None:
    if event.phase == "start":
        span["ts"] = event.ts
        span["args_summary"] = event.args_summary or span["args_summary"]
    elif event.phase == "end":
        span["status"] = event.status
        span["duration_ms"] = event.duration_ms
        span["result_summary"] = event.result_summary or span["result_summary"]
        span["tokens_in"] = event.tokens_in
        span["tokens_out"] = event.tokens_out
        span["error_message"] = event.error_message
    span["name"] = span["name"] or event.name
    if event.artifact_id:
        span["artifact_id"] = event.artifact_id
    if event.approval_id:
        span["approval_id"] = event.approval_id


def project_timeline(record: TraceRecord) -> dict[str, Any]:
    """Project a TraceRecord into an ordered, UI-friendly span timeline (no data-URI bytes)."""
    spans: dict[str, dict[str, Any]] = {}
    for event in record.events:
        if event.event_type not in _SPAN_EVENT_TYPES:
            continue
        span_id = event.tool_call_id or event.model_call_id or event.span_id
        span = spans.get(span_id)
        if span is None:
            span = _new_span(event, span_id)
            spans[span_id] = span
        _merge(span, event)

    ordered = sorted(spans.values(), key=lambda span: span["ts"])

    def _total(field: str) -> int | None:
        values = [span[field] for span in ordered if span[field] is not None]
        return sum(values) if values else None

    return {
        "trace_id": record.trace_id,
        "session_id": record.session_id,
        "turn_id": record.turn_id,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "duration_ms": record.duration_ms,
        "tokens_in_total": _total("tokens_in"),
        "tokens_out_total": _total("tokens_out"),
        "span_count": len(ordered),
        "spans": ordered,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trace_projection.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/projection.py tests/test_trace_projection.py
git commit -m "feat(m3): add project_timeline span projection"
```

---

### Task 5: Persist the trace (wiring) + contained save failure

Wire `trace_store` into the app and the test harnesses, and have `run_and_record_trace` save the record best-effort after populating the in-memory cache.

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py` (top imports + `run_and_record_trace`)
- Modify: `src/ecommerce_agent/api/app.py` (lifespan + `create_app`)
- Modify: `tests/test_sessions_api.py` (`build_test_app`)
- Modify: `tests/test_app.py` (`use_in_memory_stores`)
- Test: `tests/test_sessions_api.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_sessions_api.py`, add the import near the other store imports:

```python
from ecommerce_agent.trace.store import InMemoryTraceStore
```

In `build_test_app`, after the `app.state.trace_records = {}` line, add:

```python
    app.state.trace_store = InMemoryTraceStore()
```

Then add these tests at the end of `tests/test_sessions_api.py`:

```python
def test_turn_persists_trace_to_store() -> None:
    app = build_test_app()
    with TestClient(app) as client:
        session_id = client.post("/api/sessions").json()["session_id"]
        turn_id = client.post(
            f"/api/sessions/{session_id}/messages", json={"message": "hello"}
        ).json()["turn_id"]
        _wait_for_trace(app, session_id, turn_id)  # cache populated by the background task

        deadline = time.monotonic() + 2.0
        record = None
        while time.monotonic() < deadline:
            record = asyncio.get_event_loop().run_until_complete(
                app.state.trace_store.get(session_id, turn_id)
            )
            if record is not None:
                break
            time.sleep(0.01)
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

    result = await post_message(
        session_id, MessageRequest(message="hi"), SimpleNamespace(app=app)
    )
    turn_id = result["turn_id"]
    await asyncio.gather(*app.state.background_tasks, return_exceptions=True)

    # The turn still completed: answer persisted to the thread, cache populated, no crash.
    types = [m.type for m in await app.state.thread_store.list_messages(session_id)]
    assert types == ["user", "agent_answer"]
    assert app.state.trace_records[session_id][turn_id].turn_id == turn_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_api.py -k "persists_trace or save_failure" -v`
Expected: FAIL — `AttributeError: ... 'State' object has no attribute 'trace_store'` is not raised (we set it), but the persistence assertion fails because `run_and_record_trace` does not yet call `trace_store.save` (the store stays empty / `get` returns `None`).

- [ ] **Step 3: Add the logger and save call in `sessions.py`**

At the top of `src/ecommerce_agent/api/sessions.py`, after `from __future__ import annotations`, add:

```python
import logging
```

and after the `router = APIRouter(...)` line:

```python
logger = logging.getLogger(__name__)
```

Then replace the `run_and_record_trace` inner function (currently around lines 261–278) with:

```python
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
                approval_client=approval_client,
            )
            trace_records = app_state.trace_records
            trace_records.setdefault(session_id, {})[turn_id] = record
            # Compatibility shortcut for the sequential live reliability harness.
            app_state.last_trace = record
            trace_store = getattr(app_state, "trace_store", None)
            if trace_store is not None:
                try:
                    await trace_store.save(record)
                except Exception:
                    logger.exception(
                        "failed to persist trace for %s/%s", session_id, turn_id
                    )
        finally:
            await registry.end_turn(session_id)
```

- [ ] **Step 4: Wire the store into the app**

In `src/ecommerce_agent/api/app.py`, add the import next to the other Mongo store imports:

```python
from ecommerce_agent.trace.mongo import MongoTraceStore
```

In `lifespan`, after the `app.state.session_store = ... MongoSessionStore.from_settings(settings)` block, add:

```python
    app.state.trace_store = getattr(
        app.state, "trace_store", None
    ) or MongoTraceStore.from_settings(settings)
```

In the `finally:` block of `lifespan`, after the `session_store_close` block, add:

```python
        trace_store_close = getattr(app.state.trace_store, "close", None)
        if callable(trace_store_close):
            trace_store_close()
```

In `create_app`, after `app.state.session_store = None`, add:

```python
    app.state.trace_store = None
```

- [ ] **Step 5: Keep `test_app.py` lifespan network-free**

In `tests/test_app.py`, add the import:

```python
from ecommerce_agent.trace.store import InMemoryTraceStore
```

and extend `use_in_memory_stores`:

```python
def use_in_memory_stores(app) -> None:  # noqa: ANN001
    app.state.thread_store = InMemoryThreadStore()
    app.state.session_store = InMemorySessionStore()
    app.state.trace_store = InMemoryTraceStore()
```

(The two `test_lifespan_closes_*` tests only hit `/health` — they run no turn, so the lazily-constructed `MongoTraceStore` makes no network call; `from_settings` and `close` are network-free, matching the existing thread/session stores.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py tests/test_app.py -v`
Expected: PASS (including `test_session_lifecycle_end_to_end`, which now uses an in-memory trace store).

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py src/ecommerce_agent/api/app.py tests/test_sessions_api.py tests/test_app.py
git commit -m "feat(m3): persist per-turn trace (best-effort, contained failure)"
```

---

### Task 6: Trace read endpoint (`GET …/turns/{turn_id}/trace`)

Returns the projected timeline; reads store→cache; `404` on unknown session or unknown turn.

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_sessions_api.py`, add the import near the other trace imports:

```python
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
```

Add these tests:

```python
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
        # Cache populated but the store never saved (the residual post-done window).
        app.state.trace_records[session_id] = {"t-cache": TraceRecord(
            session_id=session_id, turn_id="t-cache"
        )}

        body = client.get(f"/api/sessions/{session_id}/turns/t-cache/trace")
        assert body.status_code == 200
        assert body.json()["turn_id"] == "t-cache"


def test_trace_endpoint_404s_for_unknown_session_and_turn() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/turns/t1/trace").status_code == 404
        session_id = client.post("/api/sessions").json()["session_id"]
        assert client.get(f"/api/sessions/{session_id}/turns/missing/trace").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_api.py -k trace_endpoint -v`
Expected: FAIL — `404` for all (route not defined), so `test_trace_endpoint_returns_timeline` fails on `status_code == 200`.

- [ ] **Step 3: Add the loader helper and the endpoint**

In `src/ecommerce_agent/api/sessions.py`, add the imports near the top (with the other `ecommerce_agent` imports):

```python
from ecommerce_agent.trace.projection import project_timeline
from ecommerce_agent.trace.schema import TraceRecord
```

Add a loader helper next to `_require_session`:

```python
async def _load_trace_record(
    request: Request, session_id: str, turn_id: str
) -> TraceRecord | None:
    record = await request.app.state.trace_store.get(session_id, turn_id)
    if record is not None:
        return record
    return request.app.state.trace_records.get(session_id, {}).get(turn_id)
```

Add the endpoint (place it after `get_thread`, before `post_message`):

```python
@router.get("/{session_id}/turns/{turn_id}/trace")
async def get_trace(session_id: str, turn_id: str, request: Request) -> dict[str, Any]:
    await _require_session(request, session_id)
    record = await _load_trace_record(request, session_id, turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return project_timeline(record)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py -k trace_endpoint -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m3): add trace read endpoint (projected timeline, store->cache)"
```

---

### Task 7: Trace export endpoint (`GET …/turns/{turn_id}/trace/export`)

Returns the **full raw** record as a downloadable JSON attachment; same `404`s and store→cache loader.

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sessions_api.py`:

```python
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
        assert body["events"][0]["name"] == "order_query"  # full record, not the projection


def test_trace_export_404s_for_unknown_session_and_turn() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/turns/t1/trace/export").status_code == 404
        session_id = client.post("/api/sessions").json()["session_id"]
        assert (
            client.get(f"/api/sessions/{session_id}/turns/missing/trace/export").status_code == 404
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_api.py -k trace_export -v`
Expected: FAIL — `404` (route not defined) on the success case.

- [ ] **Step 3: Add the endpoint**

In `src/ecommerce_agent/api/sessions.py`, add the import near the top:

```python
from fastapi.responses import JSONResponse
```

Add the endpoint immediately after `get_trace`:

```python
@router.get("/{session_id}/turns/{turn_id}/trace/export")
async def export_trace(session_id: str, turn_id: str, request: Request) -> JSONResponse:
    await _require_session(request, session_id)
    record = await _load_trace_record(request, session_id, turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return JSONResponse(
        content=record.to_dict(),
        headers={"Content-Disposition": f'attachment; filename="trace-{turn_id}.json"'},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py -k trace_export -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m3): add trace export endpoint (full record download)"
```

---

### Task 8: Artifact list endpoint (`GET …/artifacts`)

Projects artifacts from the session's thread messages (no new store method), newest-first, attaching the owning message's correlation fields; empty list (not `404`) for a session with no charts; `404` for an unknown session.

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_sessions_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sessions_api.py`:

```python
@pytest.mark.asyncio
async def test_list_artifacts_projects_from_messages_newest_first() -> None:
    from ecommerce_agent.api.sessions import list_artifacts

    app = build_test_app()
    session_id = await app.state.session_registry.create()
    await app.state.session_store.create(session_id)

    empty = await list_artifacts(session_id, SimpleNamespace(app=app))
    assert empty["artifacts"] == []

    await app.state.thread_store.append(ThreadMessage(
        session_id=session_id, type="agent_answer", content="a", turn_id="t1",
        result={"artifacts": [{
            "id": "c0", "kind": "image", "mime_type": "image/svg+xml",
            "src": "data:image/svg+xml,<svg/>", "tool_name": "generate_line_chart",
        }]},
    ))
    await app.state.thread_store.append(ThreadMessage(
        session_id=session_id, type="agent_answer", content="b", turn_id="t2",
        result={"artifacts": [{
            "id": "c1", "kind": "image", "mime_type": "image/png",
            "src": "data:image/png;base64,AAAA", "tool_name": "generate_bar_chart",
        }]},
    ))

    body = await list_artifacts(session_id, SimpleNamespace(app=app))
    artifacts = body["artifacts"]
    assert [a["id"] for a in artifacts] == ["c1", "c0"]  # newest message first
    assert artifacts[0]["turn_id"] == "t2"
    assert artifacts[0]["mime_type"] == "image/png"
    assert artifacts[0]["message_id"]  # owning message id present
    assert artifacts[0]["created_at"]
    assert artifacts[1]["tool_name"] == "generate_line_chart"


def test_list_artifacts_404_for_unknown_session() -> None:
    with TestClient(build_test_app()) as client:
        assert client.get("/api/sessions/ghost/artifacts").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sessions_api.py -k list_artifacts -v`
Expected: FAIL — `ImportError: cannot import name 'list_artifacts'`.

- [ ] **Step 3: Add the extractor helper and the endpoint**

In `src/ecommerce_agent/api/sessions.py`, add the extractor helper (near `_require_session`):

```python
def _session_artifacts(messages: list[ThreadMessage]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for message in reversed(messages):  # newest message first
        items = (message.result or {}).get("artifacts")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            artifacts.append(
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "mime_type": item.get("mime_type"),
                    "src": item.get("src"),
                    "tool_name": item.get("tool_name"),
                    "turn_id": message.turn_id,
                    "trace_id": message.trace_id,
                    "created_at": message.created_at,
                    "message_id": message.message_id,
                }
            )
    return artifacts
```

Add the endpoint (place it after `get_thread`):

```python
@router.get("/{session_id}/artifacts")
async def list_artifacts(session_id: str, request: Request) -> dict[str, Any]:
    await _require_session(request, session_id)
    messages = await request.app.state.thread_store.list_messages(session_id)
    return {"session_id": session_id, "artifacts": _session_artifacts(messages)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sessions_api.py -k list_artifacts -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(m3): add session artifact list endpoint"
```

---

### Task 9: Full-suite + lint green

**Files:** none (verification only).

- [ ] **Step 1: Run the full default suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions; integration tests skip without their env flags).

- [ ] **Step 2: Lint**

Run: `uv run ruff check .`
Expected: no errors (line-length 100).

- [ ] **Step 3: Commit any lint fixes (if needed)**

```bash
git add -A
git commit -m "chore(m3): lint pass for Phase 2 backend"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 TraceStore (InMemory + Mongo twins, save/get/ping, from_dict, unique-by-turn) → Tasks 1–3. ✓
- §3.2 persistence in the background task (cache then save), `run_turn` Mongo-free, lifespan wiring + close → Task 5. ✓
- §3.3 `project_timeline` (merge start/end, order by ts, token totals, surface artifact_id/approval_id, drop src, start-only span) → Task 4. ✓
- §3.4 read endpoint + store→cache fallback + 404s → Task 6; export endpoint (full record + Content-Disposition) + 404s → Task 7. ✓
- §4 artifact list from messages, newest-first, owning fields, empty=[] not 404, 404 unknown session → Task 8. ✓
- §7 contained save failure (log, no re-raise, cache still serves) → Task 5 (`test_trace_save_failure_is_contained`). ✓
- §8 tests: store round-trip, deserialization round-trip (Task 1), projection, read/export/artifacts endpoints, persistence wiring, save-failure → covered. ✓
- Frontend surfaces (§5, §6) → **out of scope** (separate frontend plan), as stated in the header. ✓

**2. Placeholder scan:** No TBD/TODO/"handle errors"/"similar to" — every code step shows full code. ✓

**3. Type consistency:** `TraceStore.save/get/ping` signatures match across protocol, `InMemoryTraceStore`, `MongoTraceStore`, and `_load_trace_record`. `project_timeline(record: TraceRecord) -> dict` consumes the typed record produced by both read paths (cache holds a `TraceRecord`; `MongoTraceStore.get` reconstructs one via `from_dict`). Endpoint names used in tests (`get_trace`, `export_trace`, `list_artifacts`) match the definitions. `_session_artifacts` keys match the §4 shape used by the frontend plan. ✓

**Note for the frontend plan:** the trace timeline shape is `project_timeline`'s output (Task 4); the artifact shape is `_session_artifacts`'s output (Task 8); export is `GET …/turns/{turn_id}/trace/export`. These three are the locked contract the SPA consumes.
