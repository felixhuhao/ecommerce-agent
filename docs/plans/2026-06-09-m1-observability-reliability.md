# M1 Observability + Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the M1 structured trace (one OTel-shaped event stream that SSE renders, dev debugging dumps, and the eval harness asserts over) and a `RUN_LIVE_LLM`-gated N-run reliability harness with a baseline log.

**Architecture:** A `trace` module consumes `agent.astream_events(...)` once and yields `TraceEvent`s; SSE renders from those events (replacing Week 1's direct mapping), and a `TraceRecord` accumulates per turn for JSONL dumps and eval assertions. The reliability harness drives the real stack N times, asserts structural conditions over the trace, and appends an append-only baseline record. LangSmith stays an optional, independent side-channel (not built here).

**Tech Stack:** Python 3.12, dataclasses, FastAPI/SSE, `pytest`. No new runtime deps; no datastore (local JSONL only).

**Prerequisites:** Plans 1 + 2 merged (runnable analyst, `chat.py` lazy-build). Spec: [docs/2026-06-09-week2-subagents-sandbox-design.md](../2026-06-09-week2-subagents-sandbox-design.md) §8, §9.

**Scope note:** Plan 3 of 3. M1 trace = schema + capture + raw JSONL dump + eval baseline. No M3 operator UI, no metrics dashboard, no OTel exporter (the schema is OTel-*shaped* so M4 export is a projection).

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/ecommerce_agent/trace/__init__.py` (create) | Exports `TraceEvent`, `TraceRecord`, `capture`. |
| `src/ecommerce_agent/trace/schema.py` (create) | `TraceEvent` / `TraceRecord` dataclasses (OTel-shaped ids). |
| `src/ecommerce_agent/trace/capture.py` (create) | Map raw `astream_events` → `TraceEvent`; accumulate `TraceRecord`. |
| `src/ecommerce_agent/trace/jsonl.py` (create) | Append-only trace dump + eval baseline writer. |
| `src/ecommerce_agent/api/chat.py` (modify) | Render SSE from `TraceEvent`s via `capture`; store `last_trace`. |
| `src/ecommerce_agent/evals/__init__.py` (create) | Package marker. |
| `src/ecommerce_agent/evals/live_reliability.py` (create) | N-run structural harness + baseline append. |
| `tests/test_trace_schema.py` (create) | Schema dataclass tests. |
| `tests/test_trace_capture.py` (create) | Capture mapping tests (fake event stream). |
| `tests/test_trace_jsonl.py` (create) | JSONL dump/baseline tests. |
| `tests/test_app.py` (modify) | Assert SSE-through-trace + `last_trace` populated. |
| `tests/integration/test_live_reliability.py` (create) | `RUN_LIVE_LLM` N-run harness invocation. |

---

## Task 1: Trace schema

**Files:**
- Create: `src/ecommerce_agent/trace/__init__.py`
- Create: `src/ecommerce_agent/trace/schema.py`
- Test: `tests/test_trace_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_schema.py`:
```python
from ecommerce_agent.trace.schema import SCHEMA_VERSION, TraceEvent, TraceRecord


def test_trace_record_finish_sets_duration_and_tool_names():
    rec = TraceRecord(session_id="s1", turn_id="t1")
    rec.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="start"))
    rec.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    rec.events.append(TraceEvent(event_type="tool_call", name="get_statistics", phase="start"))
    rec.finish()

    assert rec.schema_version == SCHEMA_VERSION
    assert rec.duration_ms is not None and rec.duration_ms >= 0
    assert rec.tool_names() == ["order_query", "get_statistics"]


def test_trace_event_to_dict_is_json_native():
    ev = TraceEvent(event_type="tool_call", name="x", phase="start")
    d = ev.to_dict()
    assert d["event_type"] == "tool_call"
    assert d["name"] == "x"
    assert "span_id" in d and "trace_id" in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trace_schema.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the schema**

Create `src/ecommerce_agent/trace/__init__.py`:
```python
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

__all__ = ["TraceEvent", "TraceRecord", "capture"]
```

> Note: `capture` is created in Task 2. If running Task 1 alone, temporarily make `__init__.py`
> export only schema symbols, then restore the `capture` import in Task 2.

Create `src/ecommerce_agent/trace/schema.py`:
```python
from __future__ import annotations

import dataclasses
import time
import uuid
from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class TraceEvent:
    """A span-like event. OTel-shaped ids (trace_id/span_id/parent_span_id)."""

    event_type: str  # model_call | tool_call | sandbox_exec | artifact | error | answer_chunk
    name: str | None = None
    span_id: str = field(default_factory=new_id)
    parent_span_id: str | None = None
    run_id: str | None = None
    trace_id: str | None = None
    phase: str | None = None  # start | end (for tool_call)
    status: str = "ok"  # ok | error | degraded
    ts: float = field(default_factory=time.time)
    duration_ms: float | None = None
    args_summary: str | None = None
    result_summary: str | None = None
    error_message: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    # reserved correlation ids (populated as features land)
    model_call_id: str | None = None
    tool_call_id: str | None = None
    sandbox_exec_id: str | None = None
    artifact_id: str | None = None
    approval_id: str | None = None  # M2
    execution_id: str | None = None  # M2

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class TraceRecord:
    """One chat turn (or one live-eval attempt)."""

    trace_id: str = field(default_factory=new_id)
    schema_version: str = SCHEMA_VERSION
    session_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    actor: dict | None = None
    model: dict | None = None
    prompt_version: str | None = None
    git_commit: str | None = None
    dependency_versions: dict | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    duration_ms: float | None = None
    answer: str = ""
    events: list[TraceEvent] = field(default_factory=list)

    def finish(self) -> None:
        self.ended_at = time.time()
        self.duration_ms = (self.ended_at - self.started_at) * 1000.0

    def tool_names(self) -> list[str]:
        return [
            e.name
            for e in self.events
            if e.event_type == "tool_call" and e.phase == "start" and e.name
        ]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trace_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/__init__.py src/ecommerce_agent/trace/schema.py tests/test_trace_schema.py
git commit -m "feat(trace): OTel-shaped TraceEvent/TraceRecord schema"
```

---

## Task 2: Capture pipeline

**Files:**
- Create: `src/ecommerce_agent/trace/capture.py`
- Test: `tests/test_trace_capture.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_capture.py`:
```python
from collections.abc import AsyncIterator
from types import SimpleNamespace

from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord


async def _fake_raw_events() -> AsyncIterator[dict]:
    yield {"event": "on_tool_start", "name": "order_query", "run_id": "r1", "data": {"input": {"days": 180}}}
    yield {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": SimpleNamespace(content="Sales ")}}
    yield {"event": "on_chat_model_stream", "run_id": "r1", "data": {"chunk": SimpleNamespace(content="up.")}}
    yield {"event": "on_tool_end", "name": "order_query", "run_id": "r1", "data": {"output": "[...]"}}
    yield {"event": "on_chain_start", "name": "ignored", "data": {}}  # unmapped -> skipped


async def test_capture_records_tools_and_accumulates_answer():
    rec = TraceRecord()
    yielded = [te async for te in capture(_fake_raw_events(), rec)]

    types = [te.event_type for te in yielded]
    assert types == ["tool_call", "answer_chunk", "answer_chunk", "tool_call"]

    # answer_chunk is streamed but NOT stored in the record (keeps records lean)
    assert [e.event_type for e in rec.events] == ["tool_call", "tool_call"]
    assert rec.tool_names() == ["order_query"]
    assert rec.answer == "Sales up."
    assert rec.events[0].args_summary is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trace_capture.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the capture module**

Create `src/ecommerce_agent/trace/capture.py`:
```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

_SUMMARY_LIMIT = 500


def _summarize(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text[:_SUMMARY_LIMIT] + ("…" if len(text) > _SUMMARY_LIMIT else "")


def _text_from_chunk(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _to_trace_event(raw: dict, record: TraceRecord) -> TraceEvent | None:
    etype = raw.get("event")
    run_id = raw.get("run_id")
    data = raw.get("data") or {}
    parents = raw.get("parent_ids") or []
    parent = parents[-1] if parents else None

    if etype == "on_chat_model_stream":
        text = _text_from_chunk(data.get("chunk"))
        if not text:
            return None
        record.answer += text
        return TraceEvent(
            event_type="answer_chunk", trace_id=record.trace_id, run_id=run_id,
            parent_span_id=parent, result_summary=text,
        )
    if etype == "on_tool_start":
        return TraceEvent(
            event_type="tool_call", name=raw.get("name"), phase="start",
            trace_id=record.trace_id, run_id=run_id, parent_span_id=parent,
            args_summary=_summarize(data.get("input")),
        )
    if etype == "on_tool_end":
        return TraceEvent(
            event_type="tool_call", name=raw.get("name"), phase="end",
            trace_id=record.trace_id, run_id=run_id, parent_span_id=parent,
            result_summary=_summarize(data.get("output")),
        )
    return None


async def capture(raw_events: AsyncIterator[dict], record: TraceRecord) -> AsyncIterator[TraceEvent]:
    """Map raw astream_events into TraceEvents.

    Yields each mapped event for live SSE projection, and accumulates the structural
    events (everything except per-token answer_chunk) into `record`. Answer text is
    accumulated into `record.answer`.
    """
    async for raw in raw_events:
        event = _to_trace_event(raw, record)
        if event is None:
            continue
        if event.event_type != "answer_chunk":
            record.events.append(event)
        yield event
```

Restore `src/ecommerce_agent/trace/__init__.py` to the full export (it already imports `capture`).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trace_capture.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/capture.py src/ecommerce_agent/trace/__init__.py tests/test_trace_capture.py
git commit -m "feat(trace): capture astream_events into TraceEvents + record"
```

---

## Task 3: SSE renders from the trace

**Files:**
- Modify: `src/ecommerce_agent/api/chat.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Add the assertion to the existing SSE test**

In `tests/test_app.py`, extend `test_chat_stream_maps_agent_events_to_sse_frames` — after the existing asserts, add:
```python
    # SSE now renders from the structured trace; the turn's record is retained.
    record = app.state.last_trace
    assert record is not None
    assert record.tool_names() == ["inventory_query"]
    assert "Inventory looks healthy." in record.answer
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_chat_stream_maps_agent_events_to_sse_frames -v`
Expected: FAIL — `app.state.last_trace` does not exist yet.

- [ ] **Step 3: Route `chat.py` through capture**

In `src/ecommerce_agent/api/chat.py`:

Add imports:
```python
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord
```

Remove the now-unused `_text_from_chunk` function (its logic now lives in `trace/capture.py`).

Add a trace→SSE projector:
```python
def _trace_event_to_sse(event: Any) -> dict[str, str] | None:
    if event.event_type == "answer_chunk":
        return {"event": "token", "data": _json_data({"text": event.result_summary or ""})}
    if event.event_type == "tool_call":
        return {"event": "tool", "data": _json_data({"name": event.name, "phase": event.phase})}
    return None
```

Replace `_agent_sse_events` with:
```python
async def _agent_sse_events(
    agent: Any,
    message: str,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    record = TraceRecord()
    raw = agent.astream_events({"messages": [{"role": "user", "content": message}]}, version="v2")
    async for event in capture(raw, record):
        if await request.is_disconnected():
            record.finish()
            request.app.state.last_trace = record
            return
        frame = _trace_event_to_sse(event)
        if frame is not None:
            yield frame
    record.finish()
    request.app.state.last_trace = record
```

In `create_app` (in `api/app.py`), initialise the field so it always exists. Add next to the other `app.state` assignments:
```python
    app.state.last_trace = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS (the SSE contract is unchanged; the trace is now populated).

- [ ] **Step 5: Run the full default suite + lint**

Run: `uv run pytest -m "not integration and not live" -q && uv run ruff check .`
Expected: green; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/api/chat.py src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "refactor(chat): render SSE from the structured trace; retain per-turn record"
```

---

## Task 4: JSONL dump + eval baseline writer

**Files:**
- Create: `src/ecommerce_agent/trace/jsonl.py`
- Test: `tests/test_trace_jsonl.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_trace_jsonl.py`:
```python
import json

from ecommerce_agent.trace.jsonl import append_eval_baseline, dump_trace
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def test_dump_trace_appends_one_json_line(tmp_path):
    path = tmp_path / "traces" / "trace.jsonl"
    rec = TraceRecord(session_id="s1")
    rec.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="start"))
    rec.finish()

    dump_trace(rec, str(path))
    dump_trace(rec, str(path))

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["session_id"] == "s1"
    assert parsed["events"][0]["name"] == "order_query"


def test_append_eval_baseline_appends_record(tmp_path):
    path = tmp_path / "evals" / "baseline.jsonl"
    append_eval_baseline({"n": 5, "pass_rate": 0.8}, str(path))
    parsed = json.loads(path.read_text().strip())
    assert parsed["pass_rate"] == 0.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trace_jsonl.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the writer**

Create `src/ecommerce_agent/trace/jsonl.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from ecommerce_agent.trace.schema import TraceRecord


def _append_line(obj: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(obj, default=str) + "\n")


def dump_trace(record: TraceRecord, path: str) -> None:
    """Append one TraceRecord as a JSON line (dev/eval inspection; no datastore)."""
    _append_line(record.to_dict(), path)


def append_eval_baseline(entry: dict, path: str) -> None:
    """Append one eval-batch baseline record as a JSON line."""
    _append_line(entry, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_trace_jsonl.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/trace/jsonl.py tests/test_trace_jsonl.py
git commit -m "feat(trace): append-only JSONL trace dump + eval baseline writer"
```

---

## Task 5: Live reliability harness

**Files:**
- Create: `src/ecommerce_agent/evals/__init__.py`
- Create: `src/ecommerce_agent/evals/live_reliability.py`
- Test: `tests/integration/test_live_reliability.py`

- [ ] **Step 1: Write the harness (pure logic first, with a structural-assert function unit-tested without an LLM)**

Create `src/ecommerce_agent/evals/__init__.py` (empty).

Create `src/ecommerce_agent/evals/live_reliability.py`:
```python
"""On-demand N-run reliability harness for the M1 forecast hero.

Drives the real stack N times, asserts STRUCTURAL conditions over the trace (no
LLM-as-judge), and reports pass rate + failure reasons. Gated by RUN_LIVE_LLM at the
pytest layer; this module is import-safe and its assertion logic is unit-testable.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
import time
from dataclasses import dataclass, field

from ecommerce_agent.mcp_client import WRITE_OR_APPROVAL_SPRING_TOOLS
from ecommerce_agent.trace.schema import TraceRecord

HERO_PROMPT = (
    "Which categories are trending up or down over the last 6 months, forecast next "
    "month's sales, and chart the result. Keep the summary short."
)


@dataclass
class AttemptResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def assess_attempt(record: TraceRecord, stream_body: str) -> AttemptResult:
    """Structural pass/fail for one hero attempt. No semantic judgement."""
    failures: list[str] = []
    tools = set(record.tool_names())

    if "order_query" not in tools:
        failures.append("order_query not called")
    leaked = tools & set(WRITE_OR_APPROVAL_SPRING_TOOLS)
    if leaked:
        failures.append(f"write/approval tools appeared: {sorted(leaked)}")
    if not ({"execute"} & tools or "generate_visualization" in tools):
        failures.append("neither execute nor generate_visualization was called")
    if "event: error" in stream_body or "event: done" not in stream_body:
        failures.append("stream did not complete cleanly")

    return AttemptResult(passed=not failures, failures=failures)


def _prompt_hash() -> str:
    """Hash of the analyst prompt — the primary attribution signal for prompt changes."""
    from ecommerce_agent.prompts.loader import get_prompt

    return hashlib.sha256(get_prompt("sales_analyst").encode("utf-8")).hexdigest()[:16]


def _run_metadata(settings) -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        commit = None
    deps = {}
    for pkg in ("deepagents", "langgraph", "langchain-mcp-adapters", "langchain-openai"):
        try:
            deps[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            deps[pkg] = None
    return {
        "git_commit": commit,
        "prompt_hash": _prompt_hash(),
        "dependency_versions": deps,
        "model": {
            "name": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": settings.llm_temperature,
        },
    }


def run_reliability(n, settings, *, prompt=HERO_PROMPT) -> dict:
    """Run the hero prompt `n` times against a fresh app; return a batch report dict."""
    from fastapi.testclient import TestClient

    from ecommerce_agent.api.app import create_app

    attempts: list[AttemptResult] = []
    app = create_app(settings=settings)
    with TestClient(app) as client:
        for _ in range(n):
            with client.stream("POST", "/api/chat/stream", json={"message": prompt}) as response:
                body = "".join(response.iter_text())
            record = app.state.last_trace or TraceRecord()
            attempts.append(assess_attempt(record, body))

    passed = sum(1 for a in attempts if a.passed)
    failure_modes: dict[str, int] = {}
    for a in attempts:
        for f in a.failures:
            failure_modes[f] = failure_modes.get(f, 0) + 1

    return {
        "timestamp": time.time(),
        "prompt": prompt,
        **_run_metadata(settings),
        "n": n,
        "passed": passed,
        "pass_rate": passed / n if n else 0.0,
        "failure_modes": failure_modes,
    }
```

Create `tests/integration/test_live_reliability.py` with a deterministic unit test of `assess_attempt` plus the gated live batch:
```python
import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.live_reliability import assess_attempt, run_reliability
from ecommerce_agent.trace.jsonl import append_eval_baseline
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from tests.integration.helpers import (
    skip_unless_docker_available,
    skip_unless_spring_mcp_is_running,
)


def _record_with_tools(*names):
    rec = TraceRecord()
    for n in names:
        rec.events.append(TraceEvent(event_type="tool_call", name=n, phase="start"))
    return rec


def test_assess_attempt_passes_on_good_trace():
    rec = _record_with_tools("order_query", "execute", "generate_visualization")
    result = assess_attempt(rec, "event: tool\nevent: done\n")
    assert result.passed, result.failures


def test_assess_attempt_flags_write_tool_and_missing_done():
    rec = _record_with_tools("order_query", "purchase_order_create")
    result = assess_attempt(rec, "event: error\n")
    assert not result.passed
    assert any("write/approval" in f for f in result.failures)
    assert any("did not complete" in f for f in result.failures)


@pytest.mark.integration
@pytest.mark.live
def test_live_reliability_batch_records_baseline(tmp_path):
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live reliability batch")
    skip_unless_docker_available()

    settings = Settings(mcp_request_timeout_seconds=15, mcp_sse_read_timeout_seconds=120)
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    import anyio

    anyio.from_thread  # ensure anyio importable; spring check is async
    anyio_run = __import__("asyncio").get_event_loop().run_until_complete
    anyio_run(skip_unless_spring_mcp_is_running(settings))

    n = int(os.getenv("LIVE_EVAL_RUNS", "5"))
    report = run_reliability(n, settings)
    append_eval_baseline(report, str(tmp_path / "baseline.jsonl"))

    assert report["n"] == n
    assert 0.0 <= report["pass_rate"] <= 1.0
    print("reliability:", report["passed"], "/", report["n"], report["failure_modes"])
```

- [ ] **Step 2: Run the deterministic part to verify it fails, then passes**

Run: `uv run pytest tests/integration/test_live_reliability.py -k assess -v`
Expected: FAIL first (module missing), PASS after Step 1's files exist.

- [ ] **Step 3: Run the live batch manually (stack up)**

Run: `RUN_LIVE_LLM=1 LIVE_EVAL_RUNS=5 uv run pytest tests/integration/test_live_reliability.py -k batch -s`
Expected: with Docker + sandbox image + Spring MCP + `LLM_API_KEY`, prints a pass-rate/failure-mode report and writes the baseline JSONL; otherwise a clear SKIP.

> Recommended order (spec §9): run this once against the from-scratch-codegen baseline (before
> Plan 2's helper-using prompt is fully tuned), then re-run after the helper kit + prompt land.
> A rising pass rate proves the helpers earned their place rather than tuning by vibes.

- [ ] **Step 4: Run the full default suite + lint**

Run: `uv run pytest -m "not integration and not live" -q && uv run ruff check .`
Expected: green; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/evals tests/integration/test_live_reliability.py
git commit -m "feat(evals): N-run structural reliability harness + baseline log"
```

---

## Self-Review

**Spec coverage (§8, §9):**
- §8.1 capture pipeline: one stream → TraceEvents → SSE + accumulated record; live/non-blocking; no datastore → Tasks 2, 3. ✅
- §8.2 minimal schema with OTel-shaped ids + reserved M2 `approval_id`/`execution_id` → Task 1. ✅
- §8.3 eval baseline JSONL (date, git commit, model, dep versions, N, pass rate, failure modes) → Tasks 4, 5. ✅
- §9 live reliability harness: N runs, structural assertions (order_query called, no write tools, execute-or-viz, clean done), pass-rate + failure reasons, baseline append; deterministic `assess_attempt` unit-tested → Task 5. ✅
- LangSmith independence: not built here; the own-trace reads `astream_events` directly (Task 2/3), satisfying "never load-bearing." ✅
- Correctly deferred: M3 operator UI, OTel exporter, metrics dashboard, durable audit datastore.

**Placeholder scan:** None. All code complete. The Task 1 `__init__.py` forward-reference to `capture` is resolved in Task 2.

**Type consistency:** `TraceRecord.tool_names()`/`.finish()`/`.answer`/`.events` are defined in Task 1 and used identically in Tasks 2–5. `capture(raw_events, record)` signature matches its call in Task 3's `chat.py`. `assess_attempt(record, stream_body)` and `run_reliability(n, settings)` consistent between module and tests. `dump_trace`/`append_eval_baseline` signatures match their tests. `WRITE_OR_APPROVAL_SPRING_TOOLS` is the existing Week 1 set.

> Implementation note for the executor: in Task 5's gated live test, the async `skip_unless_spring_mcp_is_running` is invoked from a sync test via the event loop; if your pytest-asyncio config makes that awkward, mark the test `async def` and `await` it directly (the `assess_*` unit tests stay sync). This does not affect the harness logic.

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-06-09-m1-observability-reliability.md`. Execute after Plans 1 and 2.

All three M1 plans are now written:
1. `2026-06-09-m1-sandbox-foundation.md`
2. `2026-06-09-m1-analyst-integration.md`
3. `2026-06-09-m1-observability-reliability.md`
