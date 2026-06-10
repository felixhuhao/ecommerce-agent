# M3 Phase 2 — Trace Timeline & Artifact Panel Design

> Design spec for the **second phase of M3**: make the agent's work inspectable and its outputs
> portable. Adds a **persisted, exposed tool/model trace timeline** and a **session-scoped artifact
> panel with downloads** to the Phase 1 operator console.
> **Phase 2 of 2 — this closes roadmap M3 acceptance; M3 is done when this ships.**
> Status: Draft | Date: 2026-06-10
> Roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) M3
> Phase 1: [2026-06-10-m3-operator-console-design.md](2026-06-10-m3-operator-console-design.md)
> Consumes: [2026-06-09-m2-approved-action-workflow-design.md](2026-06-09-m2-approved-action-workflow-design.md)

## 1. Goal & Scope

Phase 1 made the system a usable work surface (sessions, live streaming, approvals, health). Phase 2
closes the two remaining roadmap M3 acceptance lines:

- *"A human can inspect how an answer was produced."* → a **per-turn trace timeline**, backed by a
  **persisted trace** that survives restart.
- *"Generated reports/charts are session-scoped and downloadable/renderable."* → a **session-scoped
  artifact panel** with per-chart **downloads**.

**In scope:**
- **Trace persistence** — the per-turn `TraceRecord` (today in-memory only at `app.state.trace_records`)
  becomes durable in MongoDB via a new `TraceStore`.
- **Trace read + export endpoints** — a projected timeline for the UI, plus a full raw-record JSON
  download.
- **Trace timeline UI** — a right-rail tab showing one turn's model/tool spans, focused by an "Inspect"
  control on each answer.
- **Artifact list endpoint** — session-scoped artifact metadata projected from the conversation thread.
- **Artifact panel UI** — a right-rail tab of chart cards that render and download as files.

**Out of scope (cut lines, §9):** no report generator and no non-image artifact types; no
byte-streaming artifact endpoint (downloads are client-side from the existing data URI — see §6, a clean
non-breaking upgrade path is preserved); no horizontal gantt trace viz; no trace search/filter; no
mid-turn (live) trace persistence; no OTel export; no Playwright E2E; no agent conversation memory.

**Stack:** unchanged from Phase 1 — FastAPI/Mongo backend; React + TypeScript + Vite SPA, React Query
for reads, native `EventSource` for the live stream (untouched here). Single-operator.

## 2. Background — what exists today

- **Trace capture.** `run_turn` ([sessions/turn.py](../src/ecommerce_agent/sessions/turn.py)) builds a
  per-turn `TraceRecord` ([trace/schema.py](../src/ecommerce_agent/trace/schema.py)) via
  [trace/capture.py](../src/ecommerce_agent/trace/capture.py): `model_call` and `tool_call` events carry
  `phase` (`start`/`end`), `name`, `status`, `ts`, `duration_ms` (computed on `end`), truncated
  `args_summary`/`result_summary` (≤500 chars), `tokens_in`/`tokens_out`, `tool_call_id`/`model_call_id`,
  `artifact`/`artifact_id`, `approval_id`. `answer_chunk` events are **not** appended to `record.events`
  (they only accumulate `record.answer`).
- **Trace lifetime.** After a turn, `post_message`'s background task stores the record at
  `app.state.trace_records[session_id][turn_id]` and `app.state.last_trace`
  ([api/sessions.py](../src/ecommerce_agent/api/sessions.py)). **In-memory only — lost on restart, exposed
  by no endpoint.** This is roadmap risk **R5**, explicitly deferred to this phase by the Phase 1 spec.
- **Artifacts.** Chart images from viz tools are extracted during capture
  (`_image_artifact_from_output`) and, in `_append_turn_result`, attached to the `agent_answer`
  message's `result.artifacts` as `{id, kind: "image", mime_type, src (data URI), tool_name}`. The
  durable home of chart bytes is therefore the **`thread_messages`** collection. `ConversationView`
  already renders them inline. What's missing is a **session-scoped panel** and **downloads**.
- **Persistence patterns.** `ThreadStore`/`SessionStore` are Protocols with `InMemory*` (tests) +
  `Mongo*` (prod) twins over a shared motor client
  ([threads/store.py](../src/ecommerce_agent/threads/store.py),
  [threads/mongo.py](../src/ecommerce_agent/threads/mongo.py),
  [sessions/store.py](../src/ecommerce_agent/sessions/store.py)). The new `TraceStore` mirrors this exactly.

## 3. Backend — Trace Persistence

### 3.1 TraceStore (new)

A `TraceStore` Protocol with two implementations, mirroring `ThreadStore`:

- `trace/store.py` — `TraceStore` Protocol + `InMemoryTraceStore` (test-only).
- `trace/mongo.py` — `MongoTraceStore` (source of truth), `from_settings`, `close`, `ping`.

Methods:
- `save(record: TraceRecord) -> None` — persist the **full** record; idempotent **upsert keyed by the
  natural read key `(session_id, turn_id)`** (a re-save for the same turn replaces, never duplicates).
  `trace_id` stays a stored/returned field, but it is **not** the lookup key — the read contract is "one
  trace per turn," so the turn is the key.
- `get(session_id: str, turn_id: str) -> TraceRecord | None` — fetch one turn's record; `None` if absent.
- `ping() -> bool` — store reachability (for `/health`, additive; not required by acceptance).

**Round-trip (de)serialization (explicit — `asdict` is one-way).** `record.to_dict()` is
`dataclasses.asdict`, which recursively converts nested `TraceEvent` dataclasses to **plain dicts**.
A naive `TraceRecord(**doc)` would leave `events` as `list[dict]`, and `project_timeline`'s attribute
access (`event.event_type`, …) would break. So `trace/schema.py` gains `TraceEvent.from_dict(d)` and
`TraceRecord.from_dict(d)` (the latter rebuilds `events` via `TraceEvent.from_dict`), both ignoring
unknown keys (forward-compat). `MongoTraceStore.get` strips Mongo's `_id` and returns
`TraceRecord.from_dict(doc)` — a real `TraceRecord` with `TraceEvent` objects, identical in shape to the
in-memory cache so both read paths feed `project_timeline` the same type (§3.4).

`MongoTraceStore` uses a new **`traces`** collection. `save` upserts with
`update_one({"session_id", "turn_id"}, {"$set": record.to_dict()}, upsert=True)`, and a **unique
compound index on `(session_id, turn_id)`** enforces exactly one record per turn (so a stray second
record with a different `trace_id` can never make `get` ambiguous). Mongo assigns `_id`; lookup is by
the turn key, not `_id`/`trace_id`. We persist the **full** record (including each event's data-URI
`artifact.src`) for export fidelity. The resulting small duplication of chart bytes (also in
`thread_messages`) is **accepted** — charts are a few KB and sessions are few; the UI-facing timeline
projection drops `src` (§3.3) so the read payload stays light.

### 3.2 Persistence wiring (no change to `run_turn`)

`run_turn` stays Mongo-free so the eval/CLI harness keeps using it unchanged. Persistence happens in
`post_message`'s existing `run_and_record_trace` background task
([api/sessions.py](../src/ecommerce_agent/api/sessions.py)): after `run_turn` returns, the task **first**
sets the in-memory cache (`trace_records[session_id][turn_id]` and `last_trace`, for harness compat),
**then** calls `trace_store.save(record)`. The `save` is wrapped in `try/except` that **logs and does
not re-raise**: a store failure must never fail the turn (the answer already streamed and persisted to
the thread) nor leave the background task with an unhandled exception, and the populated cache still
serves the read endpoint (§3.4). `app.state.trace_store` is wired in `lifespan`
([api/app.py](../src/ecommerce_agent/api/app.py)) like `thread_store`/`session_store` (default Mongo,
overridable for tests) and `close()`d on shutdown.

### 3.3 Timeline projection (pure function)

`trace/projection.py` exposes `project_timeline(record: TraceRecord) -> dict` (pure, unit-tested in
isolation). It collapses the flat event list into a UI-friendly timeline:

- **Merge** each `model_call`/`tool_call` `start`+`end` pair (grouped by `tool_call_id`/`model_call_id`)
  into **one span**: `args_summary` from the `start`; `status`, `duration_ms`, `result_summary`,
  `tokens_in`/`tokens_out`, `error_message` from the `end`. A start with no end is still emitted as a
  span (status carried from the start, `duration_ms` null).
- **Order** spans by `ts`.
- **Surface** `artifact_id` and `approval_id` on their span; **drop the data-URI `src`** (the timeline
  links to the Artifacts tab by `artifact_id` instead of re-embedding the image).
- **Turn header**: `trace_id`, `session_id`, `turn_id`, `started_at`, `ended_at`, `duration_ms`,
  `tokens_in_total`, `tokens_out_total` (summed across spans), and `span_count`.

Projected span shape:

```
{
  "kind": "model_call" | "tool_call",
  "name": str | null,
  "status": "ok" | "failed" | ...,
  "ts": float,
  "duration_ms": float | null,
  "args_summary": str | null,
  "result_summary": str | null,
  "tokens_in": int | null,
  "tokens_out": int | null,
  "span_id": str,           // tool_call_id or model_call_id
  "artifact_id": str | null,
  "approval_id": str | null,
  "error_message": str | null
}
```

### 3.4 Read & export endpoints

All reuse the existing `_require_session` helper (`404` on unknown session). Both read the trace
**store first, then fall back to the in-memory `trace_records` cache**, which covers the window after
the background task has populated the cache but before `save` lands.

**Residual race (closed on the client).** The `agent_answer` append **and** the `done` frame both fire
*inside* `run_turn`, i.e. **before** `run_and_record_trace` populates the cache or calls `save`. So for a
brief moment after the answer becomes visible, **neither** the store nor the cache holds the record and
these endpoints return `404`. This residual window is closed on the client: **TracePanel treats a `404`
as transient and retries within a short grace period** (§5.3) — the record **normally** lands
sub-second, so the retry almost always succeeds. Inspect only appears on finalized durable messages
(§5.2), so the retry is bounded and rare. Under heavy event-loop pressure a record could still miss the
grace window; that's acceptable for single-operator M3 — the user simply re-opens Inspect (a fresh
fetch) and the now-persisted trace loads. The endpoint stays simple (plain `404`); no "pending"
sentinel is introduced.

1. `GET /api/sessions/{session_id}/turns/{turn_id}/trace` → `project_timeline(record)` (§3.3). `404` if
   no record for that turn (store miss **and** cache miss).
2. `GET /api/sessions/{session_id}/turns/{turn_id}/trace/export` → the **full raw `record.to_dict()`**
   as `application/json` with `Content-Disposition: attachment; filename="trace-{turn_id}.json"` (full
   fidelity: data-URI `src` present, summaries intact). Same `404` behavior as the read endpoint.

## 4. Backend — Artifact Listing

`GET /api/sessions/{session_id}/artifacts` (Option C — list endpoint, client-side download). Reuses
`_require_session` (`404` on unknown session). It **projects from `list_messages`** — **no new
ThreadStore method** — iterating the thread, pulling each message's `result.artifacts`, and attaching
the owning message's correlation fields. Returns artifacts **newest-first** (by descending message
`seq`):

```
{
  "session_id": str,
  "artifacts": [
    {
      "id": str,
      "kind": str,            // "image"
      "mime_type": str,
      "src": str,             // data URI (small; client downloads from this)
      "tool_name": str | null,
      "turn_id": str | null,
      "trace_id": str | null,
      "created_at": str,      // owning message created_at
      "message_id": str       // owning message, for "jump to message"
    }
  ]
}
```

A session with no charts returns `{"artifacts": []}` (**not** `404`). `src` is included so the panel
renders thumbnails and downloads client-side; charts are small data URIs so the list payload stays
reasonable at Phase-2 scale.

**Artifact `id` is not guaranteed session-unique.** The id is the tool output's own id (often a
run-id) but falls back to `chart-{index}` keyed *per message*, so two messages can each carry
`chart-0`. The endpoint does **not** rewrite ids (it would break the trace span's `artifact_id`
cross-link, §3.3); instead the frontend composes a stable React key from the owning message —
`${message_id}:${id}` (§5.4). The download filename uses the bare `id` and tolerates collisions across
turns (the browser de-dupes with " (1)").

## 5. Frontend — Surfaces & State

### 5.1 Tabbed right rail

The Phase-1 right rail (Approvals + Health stacked) becomes a **4-tab rail**: **Approvals · Artifacts ·
Trace · Health**. The active tab is shell state (component state in `AppShell`, not the URL — still a
single dashboard, no routing). Approvals keeps its pending-count badge. A new `RightRail` host renders
the active tab's panel; `ApprovalWorkspace` and `HealthPanel` move under it unchanged.

### 5.2 Inspect affordance

Every durable `agent_answer` and `agent_proposal` message (those carry `turn_id`/`trace_id`) gets a
small **Inspect** control in its header. Clicking it sets shell-level `inspectedTurnId` **and** switches
the active tab to **Trace**. The provisional streaming bubble has no Inspect (no trace yet). This is the
only new piece of cross-component state beyond the active-tab value; `ConversationView` gains an
`onInspect(turnId)` prop.

### 5.3 TracePanel (Trace tab)

Given `activeSession` + `inspectedTurnId`, fetch `GET …/turns/{turn_id}/trace` via React Query. **The
query treats a `404` as transient and retries it briefly** (≈3 attempts at ~400 ms ≈ a ~1.2 s grace
window) to absorb the residual save race (§3.4); after the grace period a persistent `404` surfaces as
the inline error state below. (Non-`404` errors are not grace-retried.) Renders:
- a **turn header** — total duration, total tokens in/out, span count, a status dot;
- a **vertical span timeline**, one row per span in `ts` order: a kind icon (model vs tool — reuse
  `Wrench` for tools), the `name`, a duration chip, a status dot (`ok`/`failed`), and a click-to-expand
  body showing `args_summary`/`result_summary` (mono) and token counts. A tool span with an
  `artifact_id` shows a **"View in Artifacts"** link (switches to the Artifacts tab); a span with an
  `approval_id` links to the proposal card;
- a **"Download trace JSON"** action — `<a href={traceExportUrl(session, turn)} download>` hitting the
  export endpoint.
- **States:** no `inspectedTurnId` → "Select an answer's *Inspect* to view its trace"; loading; `404`/
  error → inline message; empty spans → "No tool or model activity recorded."

### 5.4 ArtifactPanel (Artifacts tab)

Fetch `GET …/artifacts` via React Query (refetch on the session's `done` so new charts appear). A
responsive grid of cards, newest-first, each keyed by **`${message_id}:${id}`** (the bare `id` is not
session-unique, §4):
- a **thumbnail** rendered from `src` (reuse the existing chart-frame style);
- `tool_name` + a relative `created_at`;
- a **"Jump to message"** link (scrolls the conversation to `message_id`);
- a **Download** button — `<a download={filename} href={src}>` where
  `filename = ${id}.${extFromMime(mime_type)}` (e.g. `svg`, `png`).
- **States:** loading; empty → "No charts generated in this session yet"; error → inline retry.

### 5.5 Types, client, helpers

- `types.ts` — add `TraceSpan`, `TraceTimeline`, `ArtifactSummary` (mirror §3.3/§4 shapes).
- `api/client.ts` — add `getTrace(sessionId, turnId)`, `getArtifacts(sessionId)`, and a pure
  `traceExportUrl(sessionId, turnId)` builder. **Throw a status-bearing error.** The current `json`
  helper throws `new Error(\`${res.status}\`)` (a plain `Error` whose message is the status string),
  which forces brittle string matching. Introduce `class ApiError extends Error { status: number }` and
  throw it from `json` so TracePanel's retry predicate is clean — e.g.
  `retry: (count, err) => err instanceof ApiError && err.status === 404 && count < 3`. Existing callers
  that compare `err.message` keep working (the message is still the status); this is an additive field.
- `lib/mime.ts` — `extFromMime(mime)` (`image/svg+xml`→`svg`, `image/png`→`png`, fallback `bin`).
- **No reducer changes.** Both panels are React-Query-backed reads, independent of the SSE message
  reducer — consistent with how Phase 1 separates query-backed fetches from the live reducer.
  `inspectedTurnId` + active tab are the only new shell state.

**New files:** `components/RightRail.tsx`, `components/TracePanel.tsx`, `components/ArtifactPanel.tsx`,
`lib/mime.ts`. **Modified:** `components/ConversationView.tsx` (Inspect control + `onInspect` prop),
`components/AppShell.tsx` / `App.tsx` (tab + `inspectedTurnId` state), `api/client.ts`, `types.ts`.

## 6. Download Mechanism & Upgrade Path (decision record)

Downloads are **client-side from the data URI** already present in the artifact list (`<a download>`)
and the trace export endpoint streams JSON. We deliberately **did not** add a byte-streaming
`GET …/artifacts/{id}` endpoint, because every advantage it offers — addressable per-artifact URLs,
server-set `Content-Disposition`, metadata-only listings with lazy image loads, arbitrary size/binary
streaming, CSP-friendliness — pays off mainly for **large, binary, or non-data-URI artifacts**, which
Phase 2 scopes out (charts only, all small data URIs). Adding it later is **non-breaking**: keep the
list endpoint, add `GET …/artifacts/{id}`, and repoint the frontend's `src`/download target. This
record exists so the deferral is a documented choice, not an oversight.

## 7. Error Handling

- GETs (`/trace`, `/artifacts`) use React Query retry; the panels show explicit loading, empty, `404`,
  and error states (§5.3/§5.4).
- A `404` from the trace endpoint is **grace-retried** by TracePanel (~1.2 s, §5.3) to absorb the
  residual save race (§3.4); a `404` that persists past the grace window renders inline in the
  TracePanel and does **not** clear the session or the conversation.
- The trace read endpoint's **store→cache fallback** (§3.4) covers the window after the cache is
  populated but before the async `save` lands; the brief window before *either* is populated is the
  residual race closed by the client grace-retry above.
- `trace_store.save` runs in the background task; a save failure is logged and does **not** fail the
  turn or the user-visible answer (the answer already streamed/persisted via the thread). The in-memory
  cache still serves the read endpoint in that window.
- Artifact downloads are inert client-side `<a download>` operations; a broken `src` simply fails the
  browser download with no app state change.

## 8. Testing

**Backend (pytest; extend the fake-Mongo doubles; existing conventions):**
- **TraceStore:** `InMemoryTraceStore` and the Mongo double — `save` then `get(session_id, turn_id)`
  round-trips; `get` returns `None` for an unknown turn; re-`save` for the same `(session_id, turn_id)`
  (even with a different `trace_id`) upserts to **one** record (no duplicate); `get` stays unambiguous.
- **Deserialization round-trip:** `TraceRecord.from_dict(record.to_dict())` reconstructs a record whose
  `events` are real `TraceEvent` instances (not dicts) with fields intact, and `project_timeline` runs
  on it identically to the in-memory record; `from_dict` ignores unknown keys (forward-compat) and the
  Mongo `get` path strips `_id`.
- **Projection** (pure fn): `start`+`end` merge per span id (end's `duration_ms`/`status`/
  `result_summary` + start's `args_summary`); spans ordered by `ts`; `tokens_in_total`/
  `tokens_out_total` summed; `artifact_id` surfaced while data-URI `src` is **dropped**; `approval_id`
  surfaced; a start-only span is emitted with null `duration_ms`.
- **Trace read endpoint:** returns the projected timeline for a known turn; `404` on unknown session and
  on unknown turn; the **in-memory fallback** returns a just-finished turn whose store-save hasn't landed
  (assert by populating only the cache).
- **Trace export endpoint:** returns the full raw record (data-URI `src` present, summaries intact) with
  `Content-Disposition: attachment; filename="trace-{turn_id}.json"`; `404`s mirror the read endpoint.
- **Artifacts endpoint:** lists artifacts projected from messages with owning `{turn_id, trace_id,
  created_at, message_id}`, newest-first; returns `{"artifacts": []}` (not `404`) for a session with no
  charts; `404` on unknown session.
- **Persistence wiring:** after a turn the background task calls `trace_store.save` **and** still
  populates `trace_records`/`last_trace` (harness compat unbroken).
- **Save failure is contained:** with a `trace_store.save` that raises, the turn still completes, the
  user-visible answer is unaffected, `trace_records`/`last_trace` remain populated (so the read endpoint
  still serves via the cache fallback), the failure is logged, and the background task raises **no**
  unhandled exception.

**Frontend (Vitest + React Testing Library; mocked client):**
- **TracePanel:** renders span rows with durations/tokens; expanding a span shows args/result summary;
  the "View in Artifacts" link fires the tab switch; "Download trace JSON" targets the export URL;
  no-`inspectedTurnId`, loading, `404`/error, and empty-spans states. **Grace-retry:** a `404` that
  succeeds on a retry within the grace window renders the timeline (not the error state); a `404` that
  persists past the grace window renders the inline error.
- **ArtifactPanel:** renders cards from the list; thumbnail uses `src`; the Download link has
  `download={id.ext}` from mime; "Jump to message" fires; empty + error states.
- **Shell:** Inspect on an answer sets the inspected turn and switches to the Trace tab; tab switching
  renders the corresponding panel; the Approvals badge is unaffected.
- **`extFromMime`** unit: `image/svg+xml`→`svg`, `image/png`→`png`, unknown→`bin`.
- Playwright E2E remains deferred; Phase 2 relies on component + backend tests plus a manual demo.

## 9. Acceptance (Phase 2 — closes roadmap M3)

- **Inspect** any answer → its ordered model/tool timeline with durations, token counts, and arg/result
  summaries; **it survives a server restart** (persisted in Mongo) — satisfies *"a human can inspect how
  an answer was produced."*
- The trace **downloads as a JSON file** (`trace-{turn_id}.json`).
- The **Artifacts** tab lists every chart in the session; each renders and **downloads as a file** with
  a sensible name (`{id}.{ext}`) — satisfies *"generated reports/charts are session-scoped and
  downloadable/renderable."*
- The Phase-1 core loop (send / stream / approve) is unchanged, and the live-reliability harness still
  reads `app.state.last_trace`.
- An unknown session id `404`s on the trace, export, and artifact endpoints; a turn with no record
  `404`s on the trace/export endpoints.

## 10. Risks Touched & Cut Lines

- **R5 (observability/eval blind spot) — closed.** The trace is now persisted *and* exposed; a past
  answer is inspectable after restart, removing the Phase-1 "in-memory only / unexposed" gap.
- **R12 (artifact/audit schema lock-in).** The artifact-list shape (§4) and the timeline projection
  (§3.3) are the new durable contracts; both kept minimal and additive over existing records.
- **R1 (scope vs throughput).** Phase 2 reuses the proven Phase-1 shell (tabs over a new region) and the
  existing `Mongo*` store pattern; no new infrastructure. WIP stays at one milestone.
- **Cut lines:** no report generator or non-image artifact types; no byte-streaming artifact endpoint
  (client-side data-URI download; non-breaking upgrade path in §6); no horizontal gantt trace viz; no
  trace search/filter/pagination; no live/mid-turn trace persistence (persist at turn end); no OTel
  export (schema stays OTel-shaped for later); no Playwright E2E; no agent conversation memory (the
  standing Phase-1 known limitation).

## 11. Decisions Folded In

- **Trace persistence** via a dedicated `TraceStore` (`InMemory` + `Mongo` twins), saved at turn end in
  the background task; `run_turn` stays Mongo-free for harness reuse (§3.1–3.2).
- **Full record persisted, light timeline projected, full record exported** — one storage shape serves
  both the lean UI timeline (drops data-URI `src`) and the full-fidelity export (§3.1, §3.3, §3.4).
- **Read freshness** handled in two layers: a server store→in-memory-cache fallback for the
  post-cache/pre-save window, plus a client `404` **grace-retry** in TracePanel for the residual window
  before either is populated (the answer/`done` fire inside `run_turn`, ahead of the background task)
  (§3.4, §5.3, §7).
- **Explicit round-trip (de)serialization** — `TraceRecord.from_dict`/`TraceEvent.from_dict` reconstruct
  typed records from stored dicts (`asdict` is one-way), so both read paths feed `project_timeline` the
  same type (§3.1).
- **Trace `save` failures are contained** — logged, non-raising, cache still serves reads (§3.2, §7).
- **Artifacts (Option C):** an authoritative session-scoped list endpoint projected from messages (no new
  store method), with **client-side downloads** from the data URI; byte-streaming endpoint ("A")
  deferred with a documented non-breaking upgrade path (§4, §6).
- **Tabbed right rail** (Approvals · Artifacts · Trace · Health) extends the Phase-1 shell with no new
  layout region; "Inspect" focuses the Trace tab on a turn (§5.1–5.2).
- **One spec, one plan** — trace and artifact work share the tabbed-rail surface and the per-turn/
  per-session read-API pattern; split into backend/frontend plans only if the plan proves heavy.
