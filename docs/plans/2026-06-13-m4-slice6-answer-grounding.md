# M4 Slice 6 — Answer Grounding & Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a deterministic authority badge + linked Sources to every analytical answer, and add a groundedness LLM-judge eval — all derived from the existing per-turn trace, with no agent/prompt behavior change.

**Architecture:** A pure `build_grounding(record)` projects a `TraceRecord` into a `Grounding{authority, sources}`. Authority is derived from which tools fired (`get_statistics` → authoritative; `execute` sandbox output → derived; numeric claim with neither → unverified; else not_applicable). Full tool-output `evidence` is captured on trace spans (bounded), refs+summaries ride on the thread message, and the eval/UI join evidence back by `span_id`. A groundedness eval scores answer-vs-evidence with an LLM judge.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, dataclasses, pytest (`asyncio_mode=auto`), DeepAgents (`execute`/sandbox tool), React/TypeScript (badge + Sources UI).

**Spec:** [docs/2026-06-13-m4-slice6-answer-grounding-design.md](../2026-06-13-m4-slice6-answer-grounding-design.md)

**Conventions (from the codebase):**
- Run tests: `uv run pytest <path> -v`. Lint: `uv run ruff check <path>`.
- Evals: dataset YAML + offline scorer/report tests + a RUN_LIVE_LLM integration test, mirroring `evals/tool_choice.py` and `tests/test_tool_choice.py`.
- TraceEvent/TraceRecord are dataclasses ([trace/schema.py](../../src/ecommerce_agent/trace/schema.py)); `record.events` holds `tool_call` events with `phase` `start`/`end`.
- Commit per task (TDD: failing test → run → implement → run → commit).

---

## File Structure

**New (Python)**
- `src/ecommerce_agent/trace/tools.py` — neutral trace helpers: `fired_tools`, `DATA_BEARING_TOOLS`, `GET_STATISTICS_TOOL`, `EXECUTE_TOOL`, `is_data_bearing`, `sandbox_evidence_fired`.
- `src/ecommerce_agent/grounding/__init__.py`
- `src/ecommerce_agent/grounding/model.py` — `Authority`, `GroundingSource`, `Grounding`.
- `src/ecommerce_agent/grounding/build.py` — `build_grounding(record)`.
- `src/ecommerce_agent/evals/groundedness.py` — dataset, runner, judge, scorer, report.
- `src/ecommerce_agent/evals/datasets/groundedness.yaml`

**Modified (Python)**
- `src/ecommerce_agent/config.py` — `grounding_evidence_max_chars`.
- `src/ecommerce_agent/trace/schema.py` — `TraceEvent.evidence`.
- `src/ecommerce_agent/trace/capture.py` — capture bounded `evidence` for data-bearing tools.
- `src/ecommerce_agent/trace/projection.py` — expose `evidence` on spans.
- `src/ecommerce_agent/evals/tool_choice.py` — import `fired_tools` from `trace/tools.py` (DRY).
- `src/ecommerce_agent/threads/messages.py` — `grounding` field.
- `src/ecommerce_agent/sessions/turn.py` — attach grounding; pass evidence cap.
- `src/ecommerce_agent/api/sessions.py` — pass `evidence_max_chars` into `run_turn`.
- `src/ecommerce_agent/cli.py` — `eval groundedness`.

**New tests**
- `tests/test_trace_tools.py`, `tests/test_grounding_build.py`, `tests/test_grounding_turn.py`,
  `tests/test_evidence_capture.py`, `tests/test_groundedness_eval.py`, `tests/integration/test_groundedness_live.py`

**Modified tests:** `tests/test_tool_choice.py` (import move), `tests/test_config.py`, `tests/test_cli.py`, `tests/test_trace_projection.py`.

**Frontend (Task 9):** `frontend/src/types.ts` DTO updates, confidence-badge + Sources-expander components + tests (match existing patterns).

---

## Task 1: `trace/tools.py` — neutral trace helpers

**Files:**
- Create: `src/ecommerce_agent/trace/tools.py`
- Modify: `src/ecommerce_agent/evals/tool_choice.py`
- Test: `tests/test_trace_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_tools.py
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from ecommerce_agent.trace.tools import (
    DATA_BEARING_TOOLS,
    EXECUTE_TOOL,
    GET_STATISTICS_TOOL,
    fired_tools,
    is_data_bearing,
    sandbox_evidence_fired,
)


def _rec(*events: TraceEvent) -> TraceRecord:
    return TraceRecord(events=list(events))


def _start(name: str) -> TraceEvent:
    return TraceEvent(event_type="tool_call", name=name, phase="start")


def _end(name: str) -> TraceEvent:
    return TraceEvent(event_type="tool_call", name=name, phase="end")


def test_fired_tools_dedupes_first_seen():
    rec = _rec(_start("order_query"), _start("get_statistics"), _start("order_query"))
    assert fired_tools(rec) == ["order_query", "get_statistics"]


def test_data_bearing_allowlist():
    assert is_data_bearing(GET_STATISTICS_TOOL)
    assert is_data_bearing("order_query")
    assert is_data_bearing(EXECUTE_TOOL)
    assert not is_data_bearing("write_file")
    assert not is_data_bearing("generate_line_chart")
    assert not is_data_bearing("request_approval")


def test_sandbox_evidence_fired_needs_execute_end_with_output():
    assert not sandbox_evidence_fired(_rec(_start("execute")))
    assert not sandbox_evidence_fired(
        _rec(
            _start("execute"),
            TraceEvent(event_type="tool_call", name="execute", phase="end"),
        )
    )
    assert sandbox_evidence_fired(
        _rec(
            _start("execute"),
            TraceEvent(
                event_type="tool_call",
                name="execute",
                phase="end",
                result_summary="forecast=1250",
            ),
        )
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_trace_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: ecommerce_agent.trace.tools`.

- [ ] **Step 3: Implement**

```python
# src/ecommerce_agent/trace/tools.py
from __future__ import annotations

from ecommerce_agent.mcp_client import READ_ONLY_SPRING_TOOLS
from ecommerce_agent.tools.staging import STAGE_SALES_ANALYSIS_TOOL_NAME
from ecommerce_agent.trace.schema import TraceRecord

GET_STATISTICS_TOOL = "get_statistics"
EXECUTE_TOOL = "execute"  # DeepAgents sandbox code-execution tool (see graph.py)

# Tool calls whose output is evidence for an analytical claim. Explicit allowlist:
# DeepAgents filesystem/scaffolding tools (write_file, read_file, ls, edit_file,
# write_todos, task), viz tools, and request_approval are intentionally excluded.
DATA_BEARING_TOOLS: frozenset[str] = (
    READ_ONLY_SPRING_TOOLS | {STAGE_SALES_ANALYSIS_TOOL_NAME, EXECUTE_TOOL}
)


def fired_tools(record: TraceRecord) -> list[str]:
    """Tool names from tool_call start events, deduped in first-seen order."""
    names: list[str] = []
    for event in record.events:
        if event.event_type != "tool_call" or event.phase != "start" or not event.name:
            continue
        if event.name not in names:
            names.append(event.name)
    return names


def is_data_bearing(tool_name: str | None) -> bool:
    return tool_name in DATA_BEARING_TOOLS


def sandbox_evidence_fired(record: TraceRecord) -> bool:
    """True if a sandbox code-execution (`execute`) span completed with output."""
    return any(
        event.event_type == "tool_call"
        and event.phase == "end"
        and event.name == EXECUTE_TOOL
        and event.status == "ok"
        and bool(event.evidence or event.result_summary)
        for event in record.events
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_trace_tools.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Point slice 4 at the shared helper (DRY)**

In `src/ecommerce_agent/evals/tool_choice.py`, delete the local `fired_tools` function (lines ~119-128) and import it instead. Near the top imports add:
```python
from ecommerce_agent.trace.tools import fired_tools
```

- [ ] **Step 6: Run to verify slice 4 still green**

Run: `uv run pytest tests/test_tool_choice.py tests/test_trace_tools.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/trace/tools.py src/ecommerce_agent/evals/tool_choice.py tests/test_trace_tools.py
git commit -m "feat(trace): neutral trace tool helpers (fired_tools, data-bearing allowlist)"
```

---

## Task 2: Bounded `evidence` capture on data-bearing tool spans

**Files:**
- Modify: `src/ecommerce_agent/config.py`
- Modify: `src/ecommerce_agent/trace/schema.py`
- Modify: `src/ecommerce_agent/trace/capture.py`
- Modify: `src/ecommerce_agent/trace/projection.py`
- Test: `tests/test_evidence_capture.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_capture.py
import asyncio

from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord


async def _run(raw_events, **kwargs):
    record = TraceRecord()

    async def gen():
        for e in raw_events:
            yield e

    out = [e async for e in capture(gen(), record, **kwargs)]
    return record, out


async def test_evidence_captured_for_data_bearing_tool_and_capped():
    big = "x" * 5000
    raw = [
        {"event": "on_tool_start", "name": "get_statistics", "run_id": "r1", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "get_statistics", "run_id": "r1", "data": {"output": big}},
    ]
    record, _ = await _run(raw, evidence_max_chars=2000)
    end = next(e for e in record.events if e.phase == "end")
    assert end.evidence is not None and len(end.evidence) <= 2000
    assert end.result_summary is not None and len(end.result_summary) <= 504  # 500 + "..."


async def test_no_evidence_for_non_data_bearing_tool():
    raw = [
        {"event": "on_tool_start", "name": "write_file", "run_id": "r2", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "write_file", "run_id": "r2", "data": {"output": "ok"}},
    ]
    record, _ = await _run(raw, evidence_max_chars=2000)
    end = next(e for e in record.events if e.phase == "end")
    assert end.evidence is None
```

```python
# tests/test_config.py  (add)
def test_grounding_evidence_default():
    from ecommerce_agent.config import Settings

    assert Settings(_env_file=None).grounding_evidence_max_chars == 2000
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_evidence_capture.py tests/test_config.py::test_grounding_evidence_default -v`
Expected: FAIL (`capture()` has no `evidence_max_chars`; `TraceEvent` has no `evidence`; setting missing).

- [ ] **Step 3: Add the config setting**

In `src/ecommerce_agent/config.py` `Settings`, after the auth/audit settings block:
```python
    # M4 slice 6: answer grounding
    grounding_evidence_max_chars: int = Field(default=2000, gt=0)
```

- [ ] **Step 4: Add the `evidence` field**

In `src/ecommerce_agent/trace/schema.py` `TraceEvent`, after `result_summary`:
```python
    evidence: str | None = None
```

- [ ] **Step 5: Capture evidence in `capture()`**

In `src/ecommerce_agent/trace/capture.py`:

Add the import and a helper near the top:
```python
from ecommerce_agent.trace.tools import is_data_bearing


def _evidence(value: Any, *, cap: int) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    return text[:cap]
```

Thread the cap through. Change `_to_trace_event` signature and the `on_tool_end` branch:
```python
def _to_trace_event(
    raw: dict,
    record: TraceRecord,
    model_chunks: dict[str, str],
    evidence_max_chars: int,
) -> TraceEvent | None:
    ...
    if event_type == "on_tool_end":
        output = data.get("output")
        artifact = (
            _image_artifact_from_output(output, fallback_id=str(run_id) if run_id else None)
            if raw.get("name") in VIZ_TOOLS
            else None
        )
        evidence = (
            _evidence(output, cap=evidence_max_chars)
            if is_data_bearing(raw.get("name"))
            else None
        )
        return TraceEvent(
            event_type="tool_call",
            name=raw.get("name"),
            phase="end",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=_summarize(output),
            evidence=evidence,
            tool_call_id=run_id,
            artifact_id=artifact.get("id") if artifact else None,
            artifact=artifact,
            approval_id=(
                extract_approval_id(output) if raw.get("name") == "request_approval" else None
            ),
        )
```

Update `capture()` to accept and forward the cap:
```python
async def capture(
    raw_events: AsyncIterator[dict],
    record: TraceRecord,
    *,
    evidence_max_chars: int = 2000,
) -> AsyncIterator[TraceEvent]:
    model_chunks: dict[str, str] = {}
    span_starts: dict[tuple[str, str], TraceEvent] = {}
    async for raw in raw_events:
        event = _to_trace_event(raw, record, model_chunks, evidence_max_chars)
        ...  # rest unchanged
```

- [ ] **Step 6: Expose evidence in the projection**

In `src/ecommerce_agent/trace/projection.py`: add `"evidence": None` to the dict in `_new_span`, and in `_merge` under the `phase == "end"` branch add:
```python
        span["evidence"] = event.evidence or span["evidence"]
```

- [ ] **Step 7: Run to verify pass**

Run: `uv run pytest tests/test_evidence_capture.py tests/test_config.py tests/test_trace_projection.py -v`
Expected: PASS (update `tests/test_trace_projection.py` only if it asserts an exact span key set — add `evidence`).

- [ ] **Step 8: Commit**

```bash
git add src/ecommerce_agent/config.py src/ecommerce_agent/trace/schema.py src/ecommerce_agent/trace/capture.py src/ecommerce_agent/trace/projection.py tests/test_evidence_capture.py tests/test_config.py tests/test_trace_projection.py
git commit -m "feat(trace): bounded evidence capture for data-bearing tool spans"
```

---

## Task 3: `Grounding` data model

**Files:**
- Create: `src/ecommerce_agent/grounding/__init__.py`, `src/ecommerce_agent/grounding/model.py`
- Test: `tests/test_grounding_build.py` (model portion)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_build.py
from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource


def test_grounding_to_dict_roundtrips():
    g = Grounding(
        authority=Authority.AUTHORITATIVE,
        sources=[GroundingSource(span_id="s1", tool_name="get_statistics", args_summary="{}", result_summary="rows")],
    )
    d = g.to_dict()
    assert d["authority"] == "authoritative"
    assert d["sources"][0]["tool_name"] == "get_statistics"
    assert d["diagnostic"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_grounding_build.py::test_grounding_to_dict_roundtrips -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/ecommerce_agent/grounding/__init__.py  (empty)
```

```python
# src/ecommerce_agent/grounding/model.py
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import StrEnum


class Authority(StrEnum):
    AUTHORITATIVE = "authoritative"
    DERIVED = "derived"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class GroundingSource:
    span_id: str
    tool_name: str
    args_summary: str | None = None
    result_summary: str | None = None


@dataclass
class Grounding:
    authority: Authority
    sources: list[GroundingSource] = field(default_factory=list)
    diagnostic: str | None = None

    def to_dict(self) -> dict:
        return {
            "authority": self.authority.value,
            "sources": [dataclasses.asdict(s) for s in self.sources],
            "diagnostic": self.diagnostic,
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_grounding_build.py::test_grounding_to_dict_roundtrips -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/grounding/__init__.py src/ecommerce_agent/grounding/model.py tests/test_grounding_build.py
git commit -m "feat(grounding): Authority/Grounding/GroundingSource model"
```

---

## Task 4: `build_grounding(record)`

**Files:**
- Create: `src/ecommerce_agent/grounding/build.py`
- Test: `tests/test_grounding_build.py` (add)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_grounding_build.py  (add)
from ecommerce_agent.grounding.build import build_grounding, has_numeric_claim
from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def _rec(answer: str, *events: TraceEvent) -> TraceRecord:
    return TraceRecord(answer=answer, events=list(events))


def _start(name): return TraceEvent(event_type="tool_call", name=name, phase="start", tool_call_id=name)
def _end(name, result="rows", evidence="rows"):
    return TraceEvent(event_type="tool_call", name=name, phase="end", tool_call_id=name,
                      result_summary=result, evidence=evidence, args_summary="{}")


def test_authoritative_when_get_statistics_fired():
    rec = _rec("Total was $42,180.", _start("get_statistics"), _end("get_statistics"))
    g = build_grounding(rec)
    assert g.authority == Authority.AUTHORITATIVE
    assert [s.tool_name for s in g.sources] == ["get_statistics"]


def test_derived_when_execute_evidence_and_no_statistics():
    rec = _rec("Forecast is 1,250 units.",
               _start("stage_sales_analysis_inputs"), _end("stage_sales_analysis_inputs"),
               _start("execute"), _end("execute", result="forecast=1250", evidence="forecast=1250"))
    assert build_grounding(rec).authority == Authority.DERIVED


def test_execute_without_output_is_not_derived():
    rec = _rec("Forecast is 1,250 units.",
               _start("stage_sales_analysis_inputs"), _end("stage_sales_analysis_inputs"),
               _start("execute"), _end("execute", result=None, evidence=None))
    assert build_grounding(rec).authority == Authority.UNVERIFIED


def test_unverified_when_numeric_claim_but_no_authority_tool():
    rec = _rec("I count 1,240 orders.", _start("order_query"), _end("order_query"))
    assert build_grounding(rec).authority == Authority.UNVERIFIED


def test_not_applicable_when_no_numbers_no_data_tools():
    rec = _rec("Hello, how can I help?")
    g = build_grounding(rec)
    assert g.authority == Authority.NOT_APPLICABLE
    assert g.sources == []


def test_sources_exclude_viz_and_approval_and_filesystem():
    rec = _rec("Total $5.", _start("get_statistics"), _end("get_statistics"),
               _start("write_file"), _end("write_file"),
               _start("generate_line_chart"), _end("generate_line_chart"),
               _start("request_approval"), _end("request_approval"))
    names = [s.tool_name for s in build_grounding(rec).sources]
    assert names == ["get_statistics"]


def test_numeric_claim_heuristic():
    assert has_numeric_claim("revenue was $1,200")
    assert has_numeric_claim("up 12%")
    assert has_numeric_claim("about 1,240 orders")
    assert has_numeric_claim("a ratio of 3.5")
    assert not has_numeric_claim("here are the top products")
    assert not has_numeric_claim("I found 5 results")  # single digit is not a quantitative claim


def test_fail_closed_to_unverified_on_error(monkeypatch):
    rec = _rec("Total was 1,000.")
    monkeypatch.setattr("ecommerce_agent.grounding.build.fired_tools", lambda r: 1 / 0)
    g = build_grounding(rec)
    assert g.authority == Authority.UNVERIFIED
    assert g.diagnostic == "grounding_error"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_grounding_build.py -v`
Expected: FAIL with `ModuleNotFoundError: ecommerce_agent.grounding.build`.

- [ ] **Step 3: Implement**

```python
# src/ecommerce_agent/grounding/build.py
from __future__ import annotations

import re

from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import (
    GET_STATISTICS_TOOL,
    fired_tools,
    is_data_bearing,
    sandbox_evidence_fired,
)

# Currency, percentages, decimals, or bare quantities of 2+ digits (incl. thousands
# separators). Single digits ("5 results") are not treated as quantitative claims.
_NUMERIC_CLAIM = re.compile(r"\$\s?\d|\d\s?%|\b\d[\d,]*\.\d+\b|\b\d{1,3}(?:,\d{3})+\b|\b\d{2,}\b")


def has_numeric_claim(answer: str) -> bool:
    return bool(_NUMERIC_CLAIM.search(answer or ""))


def _sources(record: TraceRecord) -> list[GroundingSource]:
    sources: list[GroundingSource] = []
    for event in record.events:
        if event.event_type != "tool_call" or event.phase != "end":
            continue
        if not is_data_bearing(event.name):
            continue
        sources.append(
            GroundingSource(
                span_id=event.tool_call_id or event.span_id,
                tool_name=event.name,
                args_summary=event.args_summary,
                result_summary=event.result_summary,
            )
        )
    return sources


def build_grounding(record: TraceRecord) -> Grounding:
    """Project a turn's trace into a deterministic Grounding. Best-effort, fail-closed."""
    try:
        fired = fired_tools(record)
        sources = _sources(record)
        numeric = has_numeric_claim(record.answer)
        if GET_STATISTICS_TOOL in fired:
            authority = Authority.AUTHORITATIVE
        elif sandbox_evidence_fired(record):
            authority = Authority.DERIVED
        elif numeric:
            authority = Authority.UNVERIFIED
        else:
            authority = Authority.NOT_APPLICABLE
        return Grounding(authority=authority, sources=sources)
    except Exception:
        if has_numeric_claim(getattr(record, "answer", "")):
            return Grounding(authority=Authority.UNVERIFIED, diagnostic="grounding_error")
        return Grounding(authority=Authority.NOT_APPLICABLE, diagnostic="grounding_error")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_grounding_build.py -v`
Expected: PASS (all build + model tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/grounding/build.py tests/test_grounding_build.py
git commit -m "feat(grounding): deterministic build_grounding from trace"
```

---

## Task 5: Attach grounding to thread messages

**Files:**
- Modify: `src/ecommerce_agent/threads/messages.py`
- Modify: `src/ecommerce_agent/sessions/turn.py`
- Modify: `src/ecommerce_agent/api/sessions.py`
- Test: `tests/test_grounding_turn.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_grounding_turn.py
from ecommerce_agent.sessions.turn import _grounding_payload
from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def _rec(answer, *events):
    return TraceRecord(answer=answer, events=list(events))


def test_grounding_payload_for_authoritative_answer():
    rec = _rec(
        "Total was $42,180.",
        TraceEvent(event_type="tool_call", name="get_statistics", phase="start", tool_call_id="g1"),
        TraceEvent(event_type="tool_call", name="get_statistics", phase="end", tool_call_id="g1",
                   result_summary="rows", evidence="rows", args_summary="{}"),
    )
    payload = _grounding_payload(rec)
    assert payload["authority"] == "authoritative"
    assert payload["sources"][0]["span_id"] == "g1"
    # evidence is NOT carried on the message payload (it lives on the trace span)
    assert "evidence" not in payload["sources"][0]


def test_grounding_payload_none_for_not_applicable():
    assert _grounding_payload(_rec("Hi there.")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_grounding_turn.py -v`
Expected: FAIL (`_grounding_payload` missing).

- [ ] **Step 3: Add the `grounding` field to `ThreadMessage`**

In `src/ecommerce_agent/threads/messages.py`, in the "Type-specific fields" block:
```python
    grounding: dict[str, Any] | None = None
```

- [ ] **Step 4: Implement `_grounding_payload` and attach it in `turn.py`**

In `src/ecommerce_agent/sessions/turn.py`, add the import and helper:
```python
from ecommerce_agent.grounding.build import build_grounding
from ecommerce_agent.grounding.model import Authority


def _grounding_payload(record: TraceRecord) -> dict | None:
    grounding = build_grounding(record)
    if grounding.authority == Authority.NOT_APPLICABLE and not grounding.diagnostic:
        return None
    return grounding.to_dict()
```

In `_append_turn_result`, attach it to the `agent_answer` message (the no-approval branch) and the `agent_proposal` message:
```python
        grounding = _grounding_payload(record)
        ...
            ThreadMessage(
                session_id=session_id,
                type="agent_answer",
                content=record.answer,
                turn_id=turn_id,
                trace_id=record.trace_id,
                actor_id="agent",
                result={"artifacts": artifacts} if artifacts else None,
                grounding=grounding,
            ),
```
and likewise pass `grounding=_grounding_payload(record)` on the `agent_proposal` `ThreadMessage`. Leave the failure-path messages (`_proposal_failure_message`, `_proposal_fetch_failure_message`, the turn-error answer) without grounding.

- [ ] **Step 5: Pass the evidence cap through `run_turn`**

In `src/ecommerce_agent/sessions/turn.py`, add an `evidence_max_chars: int = 2000` keyword param to `run_turn` and forward it:
```python
async def run_turn(
    *,
    agent: Any,
    message: str,
    session_id: str,
    turn_id: str,
    store: ThreadStore,
    bus: SessionBus,
    recursion_limit: int,
    approval_client: Any | None = None,
    evidence_max_chars: int = 2000,
) -> TraceRecord:
    ...
    async for event in capture(raw_events, record, evidence_max_chars=evidence_max_chars):
```

In `src/ecommerce_agent/api/sessions.py` `post_message`, pass it from settings in the `run_turn(...)` call:
```python
            record = await run_turn(
                agent=runtime.agent,
                message=payload.message,
                session_id=session_id,
                turn_id=turn_id,
                store=store,
                bus=bus,
                recursion_limit=settings.agent_recursion_limit,
                approval_client=approval_client,
                evidence_max_chars=settings.grounding_evidence_max_chars,
            )
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_grounding_turn.py tests/test_session_turn.py tests/test_sessions_api.py -v`
Expected: PASS (existing turn/api tests stay green; `agent_answer` messages now carry `grounding`).

- [ ] **Step 7: Commit**

```bash
git add src/ecommerce_agent/threads/messages.py src/ecommerce_agent/sessions/turn.py src/ecommerce_agent/api/sessions.py tests/test_grounding_turn.py
git commit -m "feat(grounding): attach grounding to answer/proposal messages"
```

---

## Task 6: Groundedness eval — model, scorer, report (fake judge)

**Files:**
- Create: `src/ecommerce_agent/evals/groundedness.py`
- Create: `src/ecommerce_agent/evals/datasets/groundedness.yaml`
- Test: `tests/test_groundedness_eval.py`

The judge is injected as a callable so offline tests use a **fake judge** (no LLM). A judgment scores
each numeric claim `supported | partial | unsupported`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_groundedness_eval.py
from ecommerce_agent.evals.groundedness import (
    ClaimVerdict,
    GroundednessCaseResult,
    aggregate,
    load_groundedness_cases,
    score_answer,
)


def fake_judge_factory(verdicts):
    def judge(answer: str, evidence: str) -> list[ClaimVerdict]:
        return list(verdicts)
    return judge


def test_score_answer_counts_verdicts():
    judge = fake_judge_factory([ClaimVerdict("supported"), ClaimVerdict("unsupported")])
    result = score_answer(case_id="c1", answer="x", evidence="e", judge=judge, authority="authoritative")
    assert result.supported == 1
    assert result.unsupported == 1
    assert result.claims == 2


def test_score_answer_bad_judgment_counts_unsupported():
    def judge(answer, evidence):
        raise ValueError("bad json")
    result = score_answer(case_id="c1", answer="x", evidence="e", judge=judge, authority="derived")
    assert result.unsupported == 1
    assert result.diagnostic is not None


def test_aggregate_unsupported_claim_rate():
    results = [
        GroundednessCaseResult(case_id="a", authority="authoritative", supported=2, partial=0, unsupported=0),
        GroundednessCaseResult(case_id="b", authority="unverified", supported=0, partial=1, unsupported=1),
    ]
    report = aggregate(results)
    assert report.n == 2
    assert report.unsupported_claim_rate == 1 / 4
    assert report.partial_rate == 1 / 4
    assert report.per_authority["authoritative"]["unsupported"] == 0


def test_dataset_loads_with_family_tags():
    cases = load_groundedness_cases()
    assert len(cases) >= 6
    assert all(c.prompt and c.tags for c in cases)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_groundedness_eval.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the dataset**

```yaml
# src/ecommerce_agent/evals/datasets/groundedness.yaml
cases:
  - id: agg-category-sales
    prompt: "what were total sales by category last month?"
    tags: [aggregate]
  - id: agg-top-sellers
    prompt: "which products are my top sellers this year?"
    tags: [aggregate]
  - id: agg-order-count
    prompt: "how many orders did we get last week?"
    tags: [aggregate]
  - id: fc-next-month
    prompt: "forecast next month's sales"
    tags: [forecast]
  - id: fc-trend
    prompt: "which categories are trending up or down over the last 6 months?"
    tags: [forecast]
  - id: fc-correlation
    prompt: "is there a correlation between price and units sold?"
    tags: [forecast]
  - id: lk-unit-cost
    prompt: "what's the unit cost of SKU-9?"
    tags: [lookup]
  - id: lk-supplier
    prompt: "who supplies SKU-3?"
    tags: [lookup]
```

- [ ] **Step 4: Implement model, loader, scorer, report**

```python
# src/ecommerce_agent/evals/groundedness.py  (offline core; runner/judge in Task 7)
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

Judge = Callable[[str, str], list["ClaimVerdict"]]


@dataclass
class GroundednessCase:
    id: str
    prompt: str
    tags: list[str]


@dataclass
class ClaimVerdict:
    verdict: str  # "supported" | "partial" | "unsupported"


@dataclass
class GroundednessCaseResult:
    case_id: str
    authority: str
    supported: int = 0
    partial: int = 0
    unsupported: int = 0
    diagnostic: str | None = None

    @property
    def claims(self) -> int:
        return self.supported + self.partial + self.unsupported


@dataclass
class GroundednessReport:
    n: int
    unsupported_claim_rate: float
    partial_rate: float
    total_claims: int
    per_authority: dict[str, dict[str, int]]
    cases: list[GroundednessCaseResult] = field(default_factory=list)


def load_groundedness_cases() -> list[GroundednessCase]:
    raw = resources.files("ecommerce_agent.evals.datasets").joinpath("groundedness.yaml").read_text()
    data = yaml.safe_load(raw)
    cases = []
    for entry in data["cases"]:
        if not entry.get("prompt") or not entry.get("tags"):
            raise ValueError(f"invalid groundedness case: {entry!r}")
        cases.append(GroundednessCase(id=entry["id"], prompt=entry["prompt"], tags=list(entry["tags"])))
    return cases


def score_answer(*, case_id: str, answer: str, evidence: str, judge: Judge, authority: str) -> GroundednessCaseResult:
    result = GroundednessCaseResult(case_id=case_id, authority=authority)
    try:
        verdicts = judge(answer, evidence)
    except Exception as exc:  # conservative: a failed judgment is one unsupported claim
        result.unsupported = 1
        result.diagnostic = f"judge_error: {type(exc).__name__}"
        return result
    for v in verdicts:
        if v.verdict == "supported":
            result.supported += 1
        elif v.verdict == "partial":
            result.partial += 1
        else:
            result.unsupported += 1
    return result


def aggregate(results: list[GroundednessCaseResult]) -> GroundednessReport:
    total = sum(r.claims for r in results)
    unsupported = sum(r.unsupported for r in results)
    partial = sum(r.partial for r in results)
    per_authority: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = per_authority.setdefault(r.authority, {"supported": 0, "partial": 0, "unsupported": 0})
        bucket["supported"] += r.supported
        bucket["partial"] += r.partial
        bucket["unsupported"] += r.unsupported
    return GroundednessReport(
        n=len(results),
        unsupported_claim_rate=(unsupported / total) if total else 0.0,
        partial_rate=(partial / total) if total else 0.0,
        total_claims=total,
        per_authority=per_authority,
        cases=results,
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_groundedness_eval.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/evals/groundedness.py src/ecommerce_agent/evals/datasets/groundedness.yaml tests/test_groundedness_eval.py
git commit -m "feat(evals): groundedness model, loader, scorer, report"
```

---

## Task 7: Groundedness eval — LLM judge + runner + live test

**Files:**
- Modify: `src/ecommerce_agent/evals/groundedness.py`
- Test: `tests/test_groundedness_eval.py` (add), `tests/integration/test_groundedness_live.py`

- [ ] **Step 1: Write the failing offline test for the judge parser**

```python
# tests/test_groundedness_eval.py  (add)
from ecommerce_agent.evals.groundedness import parse_judge_response


def test_parse_judge_response_extracts_verdicts():
    raw = '{"claims": [{"verdict": "supported"}, {"verdict": "unsupported"}]}'
    verdicts = parse_judge_response(raw)
    assert [v.verdict for v in verdicts] == ["supported", "unsupported"]


def test_parse_judge_response_rejects_unknown_verdict():
    import pytest
    with pytest.raises(ValueError):
        parse_judge_response('{"claims": [{"verdict": "maybe"}]}')
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_groundedness_eval.py::test_parse_judge_response_extracts_verdicts -v`
Expected: FAIL (`parse_judge_response` missing).

- [ ] **Step 3: Implement judge parsing + judge factory + runner**

Add to `src/ecommerce_agent/evals/groundedness.py`:
```python
import json
import re

_VALID_VERDICTS = {"supported", "partial", "unsupported"}

_JUDGE_SYSTEM = (
    "You are a strict grounding judge. Given an analytical ANSWER and the EVIDENCE "
    "(tool outputs) behind it, extract each distinct numeric claim in the answer and decide "
    "whether the evidence supports it. Reply with ONLY JSON: "
    '{"claims": [{"verdict": "supported|partial|unsupported"}]}. '
    "If the answer makes no numeric claim, return an empty claims list."
)


def parse_judge_response(text: str) -> list[ClaimVerdict]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in judge response")
    payload = json.loads(match.group(0))
    verdicts = []
    for claim in payload.get("claims", []):
        verdict = claim.get("verdict")
        if verdict not in _VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {verdict!r}")
        verdicts.append(ClaimVerdict(verdict=verdict))
    return verdicts


def make_llm_judge(model: Any) -> Judge:
    """Wrap a chat model into a Judge callable (live use)."""
    def judge(answer: str, evidence: str) -> list[ClaimVerdict]:
        response = model.invoke(
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": f"ANSWER:\n{answer}\n\nEVIDENCE:\n{evidence}"},
            ]
        )
        return parse_judge_response(getattr(response, "content", "") or "")
    return judge


def evidence_for(record: Any) -> str:
    """Join data-bearing span evidence (falls back to result_summary) for the judge."""
    from ecommerce_agent.trace.tools import is_data_bearing

    parts = []
    for event in record.events:
        if event.event_type == "tool_call" and event.phase == "end" and is_data_bearing(event.name):
            parts.append(f"[{event.name}] {event.evidence or event.result_summary or ''}")
    return "\n".join(parts)
```

- [ ] **Step 4: Run to verify the parser passes**

Run: `uv run pytest tests/test_groundedness_eval.py -v`
Expected: PASS.

- [ ] **Step 5: Write the live integration test**

```python
# tests/integration/test_groundedness_live.py
import os

import pytest

pytestmark = pytest.mark.live

RUN_LIVE = os.getenv("RUN_LIVE_LLM") == "1"


@pytest.mark.skipif(not RUN_LIVE, reason="RUN_LIVE_LLM not set")
async def test_groundedness_live_gate(tmp_path):
    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.metadata import run_metadata
    from ecommerce_agent.evals.groundedness import run_groundedness_eval
    from ecommerce_agent.trace.jsonl import append_eval_baseline

    settings = get_settings()
    report = await run_groundedness_eval(settings)
    append_eval_baseline(
        {
            **run_metadata(settings, prompt_name="sales_analyst"),
            "eval": "groundedness",
            "unsupported_claim_rate": report.unsupported_claim_rate,
            "partial_rate": report.partial_rate,
            "total_claims": report.total_claims,
            "per_authority": report.per_authority,
        },
        str(tmp_path / "groundedness-baseline.jsonl"),
    )
    assert report.unsupported_claim_rate == 0.0  # safety gate
    assert report.n >= 6
```

- [ ] **Step 6: Implement `run_groundedness_eval` (live wiring)**

Add to `src/ecommerce_agent/evals/groundedness.py`. It builds the real analyst over stub Spring tools + a NoOp sandbox backend whose `execute` returns canned analysis stdout (so forecast/`derived` cases produce an `execute` evidence span without Docker), runs each case through `capture`, computes grounding + evidence, judges, and returns an aggregate report. The live integration test writes the JSONL baseline, matching the existing eval convention.

```python
async def run_groundedness_eval(settings: Any) -> GroundednessReport:
    from ecommerce_agent.evals.tool_choice import build_stub_sales_analyst  # stub Spring tools + real model
    from ecommerce_agent.grounding.build import build_grounding
    from ecommerce_agent.models import get_primary_model
    from ecommerce_agent.trace.capture import capture
    from ecommerce_agent.trace.schema import TraceRecord

    cases = load_groundedness_cases()
    analyst = build_stub_sales_analyst(settings)  # see note below
    judge = make_llm_judge(get_primary_model(settings))
    results: list[GroundednessCaseResult] = []
    for case in cases:
        record = TraceRecord()
        raw = analyst.astream_events(
            {"messages": [{"role": "user", "content": case.prompt}]},
            config={"recursion_limit": 15},
            version="v2",
        )
        async for _ in capture(raw, record, evidence_max_chars=settings.grounding_evidence_max_chars):
            pass
        grounding = build_grounding(record)
        results.append(
            score_answer(
                case_id=case.id,
                answer=record.answer,
                evidence=evidence_for(record),
                judge=judge,
                authority=grounding.authority.value,
            )
        )
    return aggregate(results)
```

> **Executor note (read before writing):** `build_stub_sales_analyst` in `evals/tool_choice.py` builds the analyst with stub Spring tools and `backend=None`. For groundedness, forecast cases need an `execute` evidence span, so add a `backend` parameter (or a sibling `build_stub_grounding_analyst`) that wires a small `NoOpSandbox` returning canned `execute` stdout. Reuse the slice-4 stub-fidelity machinery; do not re-invent the staging stub. Confirm the `BaseSandbox` method surface in `sandbox/backend.py` (`id`, `execute`, `upload_files`, `download_files`, `close`, `idle_seconds`) when writing the NoOp backend. Baseline JSONL writing stays in the live integration test with `append_eval_baseline(..., tmp_path / "groundedness-baseline.jsonl")`, matching the existing routing/approval/tool-choice eval convention; `run_groundedness_eval` should just return a report.

- [ ] **Step 7: Run offline suite (live test skips without RUN_LIVE_LLM)**

Run: `uv run pytest tests/test_groundedness_eval.py tests/integration/test_groundedness_live.py -v`
Expected: PASS offline; the live test SKIPS. If `RUN_LIVE_LLM=1` and a provider is configured, run it to confirm the gate.

- [ ] **Step 8: Commit**

```bash
git add src/ecommerce_agent/evals/groundedness.py tests/test_groundedness_eval.py tests/integration/test_groundedness_live.py
git commit -m "feat(evals): groundedness LLM judge, runner, and live gate"
```

---

## Task 8: `eval groundedness` CLI subcommand

**Files:**
- Modify: `src/ecommerce_agent/cli.py`
- Test: `tests/test_cli.py` (add)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py  (add)
def test_eval_groundedness_parser():
    from ecommerce_agent.cli import build_parser

    args = build_parser().parse_args(["eval", "groundedness"])
    assert args.eval_target == "groundedness"


def test_eval_groundedness_dispatch(monkeypatch, capsys):
    import ecommerce_agent.cli as cli
    from ecommerce_agent.evals.groundedness import GroundednessReport

    async def fake_run(settings):
        return GroundednessReport(n=8, unsupported_claim_rate=0.0, partial_rate=0.0,
                                  total_claims=10, per_authority={})

    monkeypatch.setattr(cli, "_run_groundedness_eval", lambda: __import__("asyncio").run(fake_run(None)), raising=False)
    # Simpler: monkeypatch the module-level runner the CLI calls; see implementation.
```

> Adjust the dispatch test to match the implementation's seam (mirror how `test_cli.py` already tests `eval tool-choice`). The parser test is the load-bearing assertion.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py::test_eval_groundedness_parser -v`
Expected: FAIL (`groundedness` not in choices).

- [ ] **Step 3: Implement**

In `src/ecommerce_agent/cli.py`, add `"groundedness"` to the `eval_target` choices:
```python
    eval_parser.add_argument(
        "eval_target", choices=["routing", "approval-safety", "tool-choice", "groundedness"]
    )
```
In `run_eval_command`, add a branch mirroring `tool-choice`:
```python
    if args.eval_target == "groundedness":
        import asyncio

        from ecommerce_agent.config import get_settings
        from ecommerce_agent.evals.groundedness import run_groundedness_eval

        report = asyncio.run(run_groundedness_eval(get_settings()))
        print(f"groundedness: n={report.n} unsupported_claim_rate={report.unsupported_claim_rate:.3f}")
        print(f"per_authority={report.per_authority}")
        return
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/cli.py tests/test_cli.py
git commit -m "feat(cli): eval groundedness subcommand"
```

---

## Task 9: Frontend — confidence badge + Sources expander

**Files:**
- Modify: `frontend/src/types.ts` (DTOs), `frontend/src/api/client.ts` if the trace fetch helper needs adjustment, answer/thread components
- Create: a confidence-badge component + a Sources-expander component + tests

> **Before starting:** read `frontend/src/types.ts`, the existing answer/thread rendering, and the trace panel in `frontend/src/`
> to match the framework (Vitest/RTL), styling, and how the trace timeline (`/turns/{turn_id}/trace`)
> is already fetched/rendered. The Sources expander reuses that trace data. Run the frontend suite with
> the command in `frontend/package.json`.

- [ ] **Step 1: Extend the API types**

Add `grounding` to the thread-message type:
```typescript
export type Authority = "authoritative" | "derived" | "unverified" | "not_applicable";
export interface GroundingSource { span_id: string; tool_name: string; args_summary: string | null; result_summary: string | null; }
export interface Grounding { authority: Authority; sources: GroundingSource[]; diagnostic: string | null; }
// add `grounding?: Grounding | null;` to the ThreadMessage interface
```

- [ ] **Step 2: Write the failing component test**

```tsx
// frontend/src/ConfidenceBadge.test.tsx  (adapt path/framework to the project)
import { render, screen } from "@testing-library/react";
import { ConfidenceBadge } from "./ConfidenceBadge";

test("renders authoritative badge", () => {
  render(<ConfidenceBadge authority="authoritative" />);
  expect(screen.getByText(/authoritative/i)).toBeInTheDocument();
});

test("renders nothing for not_applicable", () => {
  const { container } = render(<ConfidenceBadge authority="not_applicable" />);
  expect(container).toBeEmptyDOMElement();
});
```

- [ ] **Step 3: Run to verify failure**

Run the frontend test command. Expected: FAIL (component missing).

- [ ] **Step 4: Implement the badge + Sources expander**

- `ConfidenceBadge`: renders a colored chip for `authoritative` (green), `derived` (blue), `unverified` (amber); renders nothing for `not_applicable`. Tooltip text per §4 of the spec.
- `Sources` expander: under an `agent_answer`/`agent_proposal` that has `grounding.sources`, render "Sources (N)"; on expand, list each source (`tool_name`, `args_summary`, `result_summary`) and link/scroll to the matching trace span by `span_id`; lazily fetch the trace (`/turns/{turn_id}/trace`) to show the fuller `evidence` field when opened.
- Render the badge next to the answer; render the Sources expander below it. Hide both when `grounding` is null.

- [ ] **Step 5: Run to verify pass**

Run the frontend test command. Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): confidence badge and Sources expander"
```

---

## Task 10: Full-suite verification

- [ ] **Step 1: Python tests** — Run: `uv run pytest -q` — Expected: all pass (integration/docker/live skip without their services).
- [ ] **Step 2: Lint** — Run: `uv run ruff check src tests` — Expected: clean.
- [ ] **Step 3: Frontend** — Run the lint + test commands from `frontend/package.json` — Expected: clean/pass.
- [ ] **Step 4: (Optional, if a provider is configured)** — Run: `RUN_LIVE_LLM=1 uv run pytest tests/integration/test_groundedness_live.py -v` — Expected: `unsupported_claim_rate == 0`.
- [ ] **Step 5: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test(m4): slice 6 full-suite verification fixups"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- §2 architecture / §4 authority taxonomy → Tasks 1, 4. §3 components → Tasks 1–9. §5 data model → Task 3.
  §6 groundedness eval → Tasks 6–8. §7 evidence fidelity (bounded `evidence`, off thread/SSE path) → Task 2 (+ Task 5 asserts evidence not on the message). §8 error handling (best-effort, fail-closed to unverified, conservative judge parse) → Tasks 4, 6. §9 testing → every task. §10 file structure → matches. §11 ACs 1–7 → Tasks 1–10. §12 R-E (pin `execute`, explicit allowlist) → Task 1.
- No gaps found.

**Placeholder scan:** Python steps carry complete code. Task 7's live wiring and Task 9 (frontend) carry concrete code/skeletons but explicitly defer to existing patterns (the NoOp/stub backend surface and the frontend framework) — flagged as read-the-file steps, the only two that require opening targets before writing. The Task 8 dispatch test notes it must mirror the existing `eval tool-choice` CLI test seam (the parser assertion is the load-bearing one).

**Type consistency:** `build_grounding(record) -> Grounding`; `Grounding{authority, sources, diagnostic}`; `GroundingSource{span_id, tool_name, args_summary, result_summary}` (no `evidence` on the message — evidence lives on `TraceEvent.evidence` and is joined by `span_id`); `fired_tools(record)`, `is_data_bearing(name)`, `sandbox_evidence_fired(record)`, `GET_STATISTICS_TOOL`, `EXECUTE_TOOL`, `DATA_BEARING_TOOLS` in `trace/tools.py`; `capture(..., evidence_max_chars=...)`; `run_turn(..., evidence_max_chars=...)`; eval `score_answer/aggregate/parse_judge_response/make_llm_judge/run_groundedness_eval` and `GroundednessReport.unsupported_claim_rate` are used consistently across tasks.
