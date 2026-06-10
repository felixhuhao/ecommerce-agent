# M3 Phase 1 — Operator Console (Core Loop) Design

> Design spec for the **first phase of M3**: a single-operator web console that makes the agent's
> work visible — conversation, live streaming, and the human approval workflow built in M2.
> **Phase 1 of 2** — Phase 2 completes roadmap M3 acceptance (tool-trace timeline + artifact/chart
> panel); **M3 stays open until Phase 2 ships.**
> Status: Draft | Date: 2026-06-10
> Roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) M3
> Consumes: [2026-06-09-m2-approved-action-workflow-design.md](2026-06-09-m2-approved-action-workflow-design.md)
> Parent design: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)

## 1. Goal & Scope

Turn the system into a usable work surface, not just an API: an operator can hold a conversation,
watch the agent stream its answer and tool activity, and approve/reject proposed business actions with
the impact in front of them — all over the M2 contract.

**Phase 1 scope (core loop first — R1):**
- **Session list + conversation view** (reload + live stream); restored sessions are messageable
  (rehydration, §3.4).
- **Streaming answer panel** (token-by-token, live tool activity).
- **Approval workspace** (server-rendered card/impact, one-click Approve → execute, Reject + reason,
  live status, execution result).
- **Health panel** (MCP servers, sandbox, model, Mongo).

**Stack:** React + TypeScript + Vite, built to static assets and served by FastAPI. Single-operator —
no login (the operator identity stays in FastAPI settings; multi-user auth/RBAC is M4).

**Phase 2 (closes roadmap M3 acceptance):** tool-trace timeline and the artifact/chart panel + report
download. **The roadmap's M3 acceptance — "inspect how an answer was produced" and "session-scoped,
downloadable artifacts" — is met by Phase 1 + Phase 2 together, not Phase 1 alone.** Phase 2 must also
revisit **trace persistence**: the current trace lives in-memory (`app.state.trace_records`) and does
not survive restart, which is insufficient for inspecting a past answer; persisting traces (e.g. Mongo)
is a Phase-2 concern. Deferred beyond M3: broad theming/polish, Playwright E2E; multi-user auth → M4.

## 2. Architecture & Delivery

A **single-page React app** (Vite). The JSON/SSE API stays under `/api` and `/health*`; FastAPI serves
the built SPA via `StaticFiles` at `/` with a catch-all so a client-side refresh returns `index.html`.
In dev, the Vite dev server proxies `/api` + `/health*` to FastAPI — one origin, no CORS config.

**Layout — a single multi-panel dashboard, not routed pages:** a left **session sidebar**, a center
**conversation/stream**, and a right **approval + health rail**. This is the operator-console shape and
the most demoable; routed list→detail pages were rejected as navigation overhead with no Phase-1 benefit.

**Data flow:** the SPA is a thin consumer of the M2 contract — `GET …/thread` for history, the SSE
`…/stream` for live `token`/`tool`/`done`/`error` + durable `thread.append`, `POST …/messages` to send,
and `POST …/approvals/{id}/approve|reject`. Approval/execution results re-enter as `thread.append`s on
the same stream — no extra channel. State: React Query for fetches plus a small reducer for the live,
SSE-driven message list (dedupe by `seq`).

## 3. Backend Additions

M3 is **not pure frontend** — the trace is unexposed, there is no way to list sessions, and a real UI
surfaces correctness gaps in the existing session-scoped endpoints. FastAPI additions (everything else
exists in [api/sessions.py](src/ecommerce_agent/api/sessions.py)):

1. **Durable session records.** On `POST /api/sessions`, write a lightweight `sessions` Mongo doc
   `{session_id, created_at, title}` (`title` null, set once from the first `user` message, truncated).
   This makes the session list survive restarts — the in-memory `SessionRegistry` is reaped — reusing
   the existing Mongo connection in [threads/mongo.py](src/ecommerce_agent/threads/mongo.py). The
   `sessions` collection is also the **authoritative existence check** for rule 5.
2. **`GET /api/sessions`** — list newest-first: `{session_id, title, created_at, last_message_preview,
   message_count}`, joining `sessions` with the latest `thread_messages` entry for the preview.
3. **`GET /api/sessions/{id}`** — minimal metadata (`title`, `created_at`, `message_count`); `404` if
   the session is unknown (rule 5).
4. **Session rehydration (restored sessions are *messageable* — not just listed).** Today
   `registry.get` raises `KeyError → 404` ([sessions.py](src/ecommerce_agent/api/sessions.py)), so a
   reaped or pre-restart session appears in the list but cannot be messaged. Fix: a
   **`get_or_create_runtime(session_id)`** path rebuilds the runtime via the existing factory when the
   session exists in the `sessions` collection but is absent in memory; ids unknown to Mongo `404`.
   **Continuity caveat (do not overstate):** rehydration restores the *runtime* only — it does **not**
   make the agent conversation-aware. `run_turn` feeds the agent **only the current user message**
   ([turn.py](src/ecommerce_agent/sessions/turn.py)), so the agent answers each message in isolation in
   *restored and live sessions alike*; the operator sees full history (it lives in Mongo), the agent does
   not. Wiring prior thread history into the agent prompt is a **known limitation, out of Phase 1**
   (§10) — a candidate fast-follow.
   *Implementation note:* run the `session_known` Mongo check and the Docker-backed `build_runtime`
   **outside** the global registry lock; hold the lock only to read/insert the cache (double-checking a
   concurrent rebuild won). One slow rebuild must not block unrelated session operations. If two requests
   rebuild the same session concurrently, the **loser must `close()` its discarded runtime** (its Docker
   sandbox) so a duplicate rebuild never leaks a container.
5. **Session existence validation on every session-scoped endpoint.** Today `GET …/thread` returns an
   empty thread for an unknown id and `GET …/stream` opens an empty stream
   ([sessions.py](src/ecommerce_agent/api/sessions.py)), silently "succeeding" for garbage ids. Fix:
   `GET …/thread`, `GET …/stream`, `POST …/messages`, and `POST …/approvals/{id}/{approve,reject}` all
   validate the `session_id` against the `sessions` collection first and **`404` if unknown** (reads
   serve from Mongo; `POST …/messages` uses `get_or_create_runtime`). A created-but-empty session is
   valid (its `sessions` doc exists even with no messages).
6. **Single in-flight turn per session (guard before any side effect).** `POST …/messages` currently
   appends the user message and then spawns a background turn unconditionally; two tabs or a
   double-click interleave **untagged** `token`/`tool` frames and break the frontend's one-turn
   assumption. Fix: **acquire the per-session turn marker first — before appending the user message,
   setting the title/preview, or spawning the turn.** A second concurrent `POST …/messages` for the same
   session returns **`409 {"error": "turn_in_progress"}`** and is **fully side-effect-free** (no user
   message appended, no title/preview/session mutation). The marker clears when the turn task finishes
   (success or failure).
7. **Health extension (lightweight, no paid calls).** Extend `GET /health` with a `components` object:
   `mongo` (motor admin `ping`), `sandbox` (Docker daemon `ping`/availability), and `model`
   (**config-only** — api key + base URL present; **never a token-spending completion**). The health
   panel reads this plus the existing `/health/mcp` (Spring/ModelScope). A deeper, opt-in model probe is
   out of Phase 1 and must be explicit if ever added.
8. **Static serving (dev/test-safe).** Mount `frontend/dist` **only if it exists** — when the frontend
   isn't built (API-only pytest, backend-first development), skip the mount and the catch-all with a
   logged warning, so app startup and the API never depend on a built SPA. When mounted, a catch-all
   returns `index.html` for non-`/api`, non-`/health*` paths. Route order must guarantee `/api/*` and
   `/health*` resolve to their handlers before the catch-all (tested — §7, using a minimal `dist`
   fixture).

## 4. Frontend Surfaces & State

Components under the 3-pane shell:

- **SessionSidebar** — `GET /api/sessions`; "New session" (`POST /api/sessions`); selects the active
  session. Shows title + last-message preview. Restored sessions are usable (rehydration, §3.4).
- **ConversationView** (center) — renders the thread by message `type` (`user`, `agent_answer`,
  `agent_proposal`, `approval_status`, `execution_result`) and a composer (textarea → `POST …/messages`).
  The composer **disables while a turn is in flight** (see turn-finalization below); a `409
  turn_in_progress` just reaffirms that state and shows **no duplicate optimistic message** — the
  **in-flight turn's** user message already streamed back as a `thread.append`, and the rejected second
  send is side-effect-free, so there is nothing to append for it.
- **ApprovalWorkspace** (right rail) — pending proposals as cards (§5).
- **HealthPanel** (right rail, collapsible) — `/health` (`components`: sandbox / model / Mongo) +
  `/health/mcp` (Spring / ModelScope); status dots, no blocking.
- **StreamProvider / useSessionStream** — owns one `EventSource` per active session.

**SSE handling (core mechanic):** opening a session opens the stream, which **replays the thread backlog
as `thread.append` then goes live** (the M2 stream already does this), so the message list is built from
`thread.append` upserts keyed by `seq` (idempotent dedupe). `GET …/thread` is the reload fallback. The
live `token`/`tool` frames are **not** turn-tagged (only `done` carries `turn_id`), so the frontend
tracks the single in-flight turn from the `POST …/messages` response (`{turn_id, user_message_id}`); the
backend single-turn guard (§3.6) keeps this to one turn per session. `token` frames accumulate into a
**provisional answer bubble** for that turn; `tool` frames drive a transient "using `inventory_query`…"
indicator.

**Turn finalization (reconnect-safe):** the in-flight turn ends when **either** the live `done` frame
arrives **or** a terminal durable message for that `turn_id` appears via `thread.append` — i.e. an
`agent_answer` (normal or `status="failed"`) or an `agent_proposal`. `done` is ephemeral SSE; a browser
that reconnects after missing it gets only the durable backlog, so the terminal durable message must
also re-enable the composer and clear the provisional bubble (which the durable `agent_answer` replaces;
a proposal turn produces no separate answer bubble). `error` shows a non-blocking toast (the durable
failure `agent_answer` still arrives and finalizes the turn).

**State shape:** per active session — a `Map<seq, ThreadMessage>` (the rendered list), an in-flight token
buffer for the active `turn_id`, and approvals derived by grouping messages on `approval_id`. TS types
for `ThreadMessage` and the SSE event envelopes live in one `src/types` module mirroring the Python
schema in [threads/messages.py](src/ecommerce_agent/threads/messages.py).

## 5. Approval Workspace UX

An `agent_proposal` message carries `approval_id`, the server-rendered `card`, `tool_name`, and
`status="pending"`. The workspace renders each as a card with a status badge.

**Card display contract (resilient — Java `operationDetail` varies by action).** `approval_card`
([approvals.py](src/ecommerce_agent/approvals.py)) returns Java's `operationDetail` merged with the
stable keys `{approvalId, toolName, operationType, status}`. The card renders:
- a **header** from the stable keys (always present): operation type + tool + status;
- the `operationDetail` fields rendered **generically** as labeled key/value rows (so a new action type
  still displays without code changes);
- a **"raw details" expander** with the full JSON as the fallback for shapes the generic renderer can't
  pretty-print.

**One human action.** **Approve** calls `POST …/approvals/{id}/approve` (FastAPI orchestrates
approve→execute server-side — one click, two auditable transitions). **Reject** takes an optional reason
and calls `POST …/approvals/{id}/reject`.

**Approval↔session binding is enforced by Java, not re-implemented in FastAPI.** The `ApprovalClient`
sends `X-Session-Id` = the URL's `session_id` ([approvals.py](src/ecommerce_agent/approvals.py)); Java's
actor/session check (Java spec §4.3) rejects any `approval_id` not bound to that session, so a
cross-session approve/reject fails at the trust boundary and FastAPI surfaces the rejection. FastAPI's
own approval-endpoint check is session *existence* only (§3.5); the cross-session rejection path is
tested (§7).

**Live lifecycle off the same stream.** The endpoints append `approval_status` and `execution_result`
messages, which arrive as `thread.append`s. The workspace folds an approval's messages on `approval_id`:
`pending → approved → consumed` (show the execution result from `result`, e.g. "PO #123 placed,
inventory +500"), or `rejected` (reason), `invalidated` ("preconditions changed — request a fresh
approval"), `failed` (error). Buttons disable while a request is in flight and once status is
non-`pending`. Multiple pending approvals each render their own card; proposals also appear inline in the
conversation as proposal bubbles.

## 6. Error Handling

- GETs use React Query retry; mutations (send / approve / reject) surface inline errors and allow retry.
- `EventSource` auto-reconnects; because the stream replays backlog on connect, reconnect re-syncs state
  (dedupe by `seq`) and finalizes any turn whose terminal durable message it now sees (§4). Show a subtle
  "reconnecting" hint.
- **`409 turn_in_progress`** on send: keep the composer disabled, show "a turn is already running," and
  do **not** add a duplicate optimistic user message; keep streaming and let the turn finalize normally.
- **Approval `409` (already decided):** reconcile by **refetching the thread** (`GET …/thread`) so the
  card folds to the server's current state — no stale `pending` card lingers.
- Other approval errors surface the Java error payload (FastAPI passes it through as the `HTTPException`
  detail) and leave the card in its last known state.
- **`404` on any session-scoped endpoint** (unknown session): refresh the list and clear the active
  session. (A merely-reaped session does **not** 404 — it rehydrates, §3.4.)
- Degraded `/health` components or `/health/mcp` show red dots without blocking use.
- Every panel has explicit loading and empty states.

## 7. Testing

- **Frontend (Vitest + React Testing Library):** a mocked API/SSE helper. Cases: `seq` dedupe;
  provisional→durable answer swap; **turn finalization by a terminal durable message when `done` was
  missed** (reconnect); composer disabled during a turn + `409 turn_in_progress` adds no duplicate
  message; approval status folding (`pending→consumed|rejected|invalidated|failed`); approval `409`
  triggers a thread refetch; card generic-render + raw-JSON fallback; session switch tears down and
  re-opens the right `EventSource`; health rendering; error/empty states.
- **Backend (pytest, existing conventions; extend the fake-Mongo doubles):**
  - `GET /api/sessions` ordering/preview/title-from-first-message; `sessions` doc written on `POST`;
    `GET /api/sessions/{id}`.
  - **Session validation:** unknown `session_id` returns `404` from `GET …/thread`, `GET …/stream`,
    `POST …/messages`, and the approval endpoints (not an empty thread/stream).
  - **Approval scoping:** approving/rejecting an `approval_id` bound to a different session is rejected
    (Java actor/session binding via `X-Session-Id`) and the rejection is surfaced; FastAPI's own check is
    session existence only.
  - **Rehydration:** a session present in `sessions` but absent from the registry is messageable (runtime
    rebuilt); an id unknown to Mongo `404`s. Concurrent rebuilds resolve to one runtime without holding
    the global lock across `build_runtime`, and the discarded loser runtime is `close()`d (no leaked
    sandbox).
  - **Single-turn guard (side-effect-free):** a second `POST …/messages` while a turn is active returns
    `409 {"error": "turn_in_progress"}` **and leaves the thread unchanged** (no stray user message, no
    title/preview write); the marker clears after completion.
  - **Health extension:** `components` reports mongo/sandbox/model; the model check makes **no** outbound
    completion call (assert via a no-network/monkeypatched model).
  - **Static serving:** with a minimal `dist` fixture, `/api/sessions`, `/health`, `/health/mcp` resolve
    to their handlers, an unknown SPA path returns `index.html`, and an unknown `/api/...` path returns
    `404` (not `index.html`); **without `frontend/dist`, the app still starts and the API tests pass**
    (no static mount).
- Playwright E2E is deferred; Phase 1 relies on component + backend tests plus a manual demo.

## 8. Project Structure

```
frontend/                     # new Vite + React + TS app
  src/
    api/        # fetch client + useSessionStream (EventSource) hook
    components/ # SessionSidebar, ConversationView, ApprovalWorkspace, HealthPanel, shell
    state/      # per-session reducer (seq map, token buffer, approval folding, turn finalization)
    types/      # ThreadMessage + SSE event envelopes (mirror of Python schema)
  dist/                       # build output, served by FastAPI
src/ecommerce_agent/
  api/sessions.py             # + list/get; session validation (404); 409 turn guard
  api/app.py                  # + /health components (mongo/sandbox/model) + StaticFiles + SPA catch-all
  sessions/registry.py        # + get_or_create_runtime (rehydrate; build outside the lock) + active-turn tracking
  threads/mongo.py            # + sessions collection (create/list/get/exists) + title
```

Frontend files stay small and single-responsibility; the per-session reducer is the one piece of real
logic and is unit-tested in isolation. The Vite build (`frontend/dist`) is mounted by FastAPI; a
`uv`/make script wires `npm run build` into the serve path for prod.

## 9. Acceptance (Phase 1)

- Operator can create and select sessions from the sidebar; the list survives a server restart, and a
  **restored/reaped session is still messageable** (rehydration). An **unknown session id `404`s** on
  every session-scoped endpoint.
- Sending a message streams the answer token-by-token with a live tool-activity indicator; a **second
  concurrent send is rejected** (`409 turn_in_progress`) with no duplicate message **and no stray thread
  entry**; the turn finalizes on `done` **or** on its terminal durable message after a reconnect.
- A proposal renders with the server-rendered impact (generic fields + raw-JSON fallback); **Approve**
  runs approve→execute and the execution result appears **live and survives reload**; **Reject** with a
  reason shows `rejected`; `invalidated`/`failed` render correctly; an approval cannot be double-acted.
- The health panel reflects Spring MCP / ModelScope / sandbox / model / Mongo, and the model check spends
  no tokens.
- A browser refresh rebuilds state from `thread` + stream; a dropped stream reconnects and re-syncs.

## 10. Risks Touched & Cut Lines

- **R1 (scope/throughput):** core loop only; trace timeline + artifact/chart panel are **M3 Phase 2**,
  which closes the roadmap's M3 acceptance — M3 stays open until then. No theming/polish until the core
  surfaces work (roadmap M3 cut line).
- **Agent not conversation-aware (known limitation).** Each turn is fed only the current message
  (§3.4); the operator sees full history but the agent does not. Phase 1 does not change this; a minimal
  history/context handoff into the agent prompt is a candidate fast-follow (its own slice), not Phase 1.
- **R5 (observability):** trace captured but unexposed and **in-memory only** — its read endpoint,
  timeline, and **persistence** are Phase 2 (a past answer is not inspectable after restart until then).
- **R7 (dependency fragility):** the `model` health check is **config-only** and never spends tokens.
- **Single-operator:** no auth in Phase 1; FastAPI holds the operator identity. Multi-user/RBAC is M4.
- **Cut lines:** no artifact store, no report download, no trace replay/persistence, no E2E harness, no
  agent conversation memory in Phase 1.

## 11. Open Decisions Folded In

- **Restored sessions are messageable** via `get_or_create_runtime` rehydration (vs read-only history);
  rehydration restores the runtime, **not** agent conversation memory (§3.4) — wording corrected from an
  earlier "loses no state" overstatement.
- **Session existence is validated on every session-scoped endpoint** (404 on unknown) rather than
  silently serving empty thread/stream (§3.5).
- **One in-flight turn per session** enforced by a backend `409 {"error": "turn_in_progress"}` guard,
  acquired **before any side effect** so a rejected send leaves the thread unchanged; the frontend adds
  no duplicate optimistic message (§3.6, §6).
- **Approval↔session binding** is enforced by Java via `X-Session-Id` (cross-session approve/reject
  rejected at the trust boundary); FastAPI checks session existence only (§5).
- **Static serving is dev/test-safe** — mounted only if `frontend/dist` exists, so API tests don't
  depend on a built SPA (§3.8).
- **Turn finalization is reconnect-safe**: `done` frame **or** terminal durable message ends the turn
  (§4).
- **This spec is M3 Phase 1**, the first slice of roadmap M3; Phase 2 (incl. trace persistence) completes
  it.
- Session list backed by a new lightweight `sessions` Mongo collection; single dashboard layout; native
  `EventSource`; `model` health is config-only (no token spend).
