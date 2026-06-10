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
- **Session list + conversation view** (reload + live stream), with restored sessions fully interactive.
- **Streaming answer panel** (token-by-token, live tool activity).
- **Approval workspace** (server-rendered card/impact, one-click Approve → execute, Reject + reason,
  live status, execution result).
- **Health panel** (MCP servers, sandbox, model, Mongo).

**Stack:** React + TypeScript + Vite, built to static assets and served by FastAPI. Single-operator —
no login (the operator identity stays in FastAPI settings; multi-user auth/RBAC is M4).

**Phase 2 (closes roadmap M3 acceptance):** tool-trace timeline (a trace-read endpoint over the data
already in `app.state.trace_records`) and the artifact/chart panel + report download. **The roadmap's
M3 acceptance — "inspect how an answer was produced" and "session-scoped, downloadable artifacts" —
is met by Phase 1 + Phase 2 together, not Phase 1 alone.** Deferred beyond M3: broad theming/polish,
Playwright E2E; multi-user auth → M4.

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

M3 is **not pure frontend** — the trace exists but is unexposed, there is no way to list sessions, and
two correctness gaps surface once a real UI drives the M2 API. FastAPI additions (everything else exists
in [api/sessions.py](src/ecommerce_agent/api/sessions.py)):

1. **Durable session records.** On `POST /api/sessions`, write a lightweight `sessions` Mongo doc
   `{session_id, created_at, title}` (`title` null, set once from the first `user` message, truncated).
   This makes the session list survive restarts — the in-memory `SessionRegistry` is reaped — reusing
   the existing Mongo connection in [threads/mongo.py](src/ecommerce_agent/threads/mongo.py).
2. **`GET /api/sessions`** — list newest-first: `{session_id, title, created_at, last_message_preview,
   message_count}`, joining `sessions` with the latest `thread_messages` entry for the preview.
3. **`GET /api/sessions/{id}`** — minimal metadata (`title`, `created_at`, `message_count`) for the
   conversation header.
4. **Session rehydration (so restored sessions are interactive — not just listed).** Today
   `registry.get` raises `KeyError → 404` ([sessions.py](src/ecommerce_agent/api/sessions.py)), so a
   session that was reaped or predates a restart appears in the list but **cannot be messaged**. Fix:
   `registry.get` rebuilds the runtime via the existing factory when the `session_id` is present in the
   `sessions` collection but absent in memory; only ids unknown to Mongo `404`. This is safe because
   `run_turn` feeds the agent **only the current message** — conversation history lives in Mongo, so a
   rebuilt runtime loses no state. (Feeding prior thread history into the agent prompt is a separate
   future concern, out of Phase 1.) The registry takes a `session_known(session_id) -> bool` async
   predicate backed by the `sessions` collection.
5. **Single in-flight turn per session.** `POST …/messages` currently spawns a background turn
   unconditionally; two tabs or a double-click interleave **untagged** `token`/`tool` frames and break
   the frontend's one-turn assumption. Fix: track an active turn per session; a second concurrent
   `POST …/messages` for the same session returns **`409 turn_in_progress`**; the marker clears when the
   turn task finishes (success or failure). (A queue was rejected as Phase-1 overkill; turn-tagging
   every live frame is the alternative if true concurrency is ever wanted.)
6. **Health extension (lightweight, no paid calls).** Extend `GET /health` with a `components` object:
   `mongo` (motor admin `ping`), `sandbox` (Docker daemon `ping`/availability), and `model`
   (**config-only** — api key + base URL present; **never a token-spending completion**). The health
   panel reads this plus the existing `/health/mcp` (Spring/ModelScope). A deeper, opt-in model probe is
   out of Phase 1 and must be explicit if ever added.
7. **Static serving.** Mount `frontend/dist`; a catch-all returns `index.html` for non-`/api`,
   non-`/health*` paths so client-side state survives refresh. Route order must guarantee `/api/*` and
   `/health*` are matched before the catch-all (tested — §7).

## 4. Frontend Surfaces & State

Components under the 3-pane shell:

- **SessionSidebar** — `GET /api/sessions`; "New session" (`POST /api/sessions`); selects the active
  session. Shows title + last-message preview. Restored sessions are fully usable (rehydration, §3.4).
- **ConversationView** (center) — renders the thread by message `type` (`user`, `agent_answer`,
  `agent_proposal`, `approval_status`, `execution_result`) and a composer (textarea → `POST …/messages`).
  The composer **disables while a turn is in flight** (from send until the `done` frame); a `409
  turn_in_progress` simply reaffirms that disabled state.
- **ApprovalWorkspace** (right rail) — pending proposals as cards (§5).
- **HealthPanel** (right rail, collapsible) — `/health` (`components`: sandbox / model / Mongo) +
  `/health/mcp` (Spring / ModelScope); status dots, no blocking.
- **StreamProvider / useSessionStream** — owns one `EventSource` per active session.

**SSE handling (core mechanic):** opening a session opens the stream, which **replays the thread
backlog as `thread.append` then goes live** (the M2 stream already does this), so the message list is
built purely from `thread.append` upserts keyed by `seq` (idempotent dedupe). `GET …/thread` is the
reload fallback. The live `token`/`tool` frames are **not** turn-tagged (only `done` carries `turn_id`),
so the frontend tracks the single in-flight turn from the `POST …/messages` response
(`{turn_id, user_message_id}`); the backend's single-turn guard (§3.5) keeps this to one turn per
session. `token` frames accumulate into a **provisional answer bubble** for that turn; when the turn's
durable `agent_answer` arrives as a `thread.append` (it carries the `turn_id`), it replaces the
provisional bubble. `tool` frames drive a transient "using `inventory_query`…" indicator; `done`
finalizes the turn; `error` shows a non-blocking toast (the durable failure `agent_answer` with
`status="failed"` still arrives via `thread.append`).

**State shape:** per active session — a `Map<seq, ThreadMessage>` (the rendered list), an in-flight
token buffer for the active `turn_id`, and approvals derived by grouping messages on `approval_id`. TS
types for `ThreadMessage` and the SSE event envelopes live in one `src/types` module mirroring the
Python schema in [threads/messages.py](src/ecommerce_agent/threads/messages.py).

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
  (dedupe by `seq`). Show a subtle "reconnecting" hint.
- **`409 turn_in_progress`** on send: keep the composer disabled and show "a turn is already running";
  the `done` frame re-enables it.
- **Approval `409` (already decided):** reconcile by **refetching the thread** (`GET …/thread`) so the
  card folds to the server's current state — no stale `pending` card lingers.
- Other approval errors surface the Java error payload (FastAPI passes it through as the `HTTPException`
  detail) and leave the card in its last known state.
- `404` (session unknown to Mongo) refreshes the list and clears the active session. (A merely-reaped
  session does **not** 404 — it rehydrates, §3.4.)
- Degraded `/health` components or `/health/mcp` show red dots without blocking use.
- Every panel has explicit loading and empty states.

## 7. Testing

- **Frontend (Vitest + React Testing Library):** a mocked API/SSE helper. Cases: `seq` dedupe;
  provisional→durable answer swap by `turn_id`; composer disabled during a turn + `409 turn_in_progress`
  handling; approval status folding (`pending→consumed|rejected|invalidated|failed`); approval `409`
  triggers a thread refetch; card generic-render + raw-JSON fallback; session switch tears down and
  re-opens the right `EventSource`; health rendering; error/empty states.
- **Backend (pytest, existing conventions; extend the fake-Mongo doubles):**
  - `GET /api/sessions` ordering/preview/title-from-first-message; `sessions` doc written on `POST`;
    `GET /api/sessions/{id}`.
  - **Rehydration:** a session present in `sessions` but absent from the registry is messageable (runtime
    rebuilt); an id unknown to Mongo `404`s.
  - **Single-turn guard:** a second `POST …/messages` while a turn is active returns `409
    turn_in_progress`; the marker clears after completion.
  - **Health extension:** `components` reports mongo/sandbox/model; the model check makes **no** outbound
    completion call (assert via a no-network/monkeypatched model).
  - **Static route order:** `/api/sessions`, `/health`, `/health/mcp` resolve to their handlers; an
    unknown SPA path returns `index.html`; an unknown `/api/...` path returns `404` (not `index.html`).
- Playwright E2E is deferred; Phase 1 relies on component + backend tests plus a manual demo.

## 8. Project Structure

```
frontend/                     # new Vite + React + TS app
  src/
    api/        # fetch client + useSessionStream (EventSource) hook
    components/ # SessionSidebar, ConversationView, ApprovalWorkspace, HealthPanel, shell
    state/      # per-session reducer (seq map, token buffer, approval folding)
    types/      # ThreadMessage + SSE event envelopes (mirror of Python schema)
  dist/                       # build output, served by FastAPI
src/ecommerce_agent/
  api/sessions.py             # + GET /api/sessions, GET /api/sessions/{id}; 409 turn guard
  api/app.py                  # + /health components (mongo/sandbox/model) + StaticFiles + SPA catch-all
  sessions/registry.py        # + rehydration (rebuild known session on get-miss) + active-turn tracking
  threads/mongo.py            # + sessions collection (create/list/get) + title
```

Frontend files stay small and single-responsibility; the per-session reducer is the one piece of real
logic and is unit-tested in isolation. The Vite build (`frontend/dist`) is mounted by FastAPI; a
`uv`/make script wires `npm run build` into the serve path for prod.

## 9. Acceptance (Phase 1)

- Operator can create and select sessions from the sidebar; the list survives a server restart, and a
  **restored/reaped session is still messageable** (rehydration).
- Sending a message streams the answer token-by-token with a live tool-activity indicator; a **second
  concurrent send is rejected** (`409 turn_in_progress`) and the composer stays disabled until `done`.
- A proposal renders with the server-rendered impact (generic fields + raw-JSON fallback); **Approve**
  runs approve→execute and the execution result appears **live and survives reload**; **Reject** with a
  reason shows `rejected`; `invalidated`/`failed` render correctly; an approval cannot be double-acted.
- The health panel reflects Spring MCP / ModelScope / sandbox / model / Mongo, and the model check spends
  no tokens.
- A browser refresh rebuilds state from `thread` + stream; a dropped stream reconnects and re-syncs.

## 10. Risks Touched & Cut Lines

- **R1 (scope/throughput):** core loop only; trace timeline + artifact/chart panel are **M3 Phase 2**,
  which is what closes the roadmap's M3 acceptance — M3 stays open until then. No theming/polish until
  the core surfaces work (roadmap M3 cut line).
- **R5 (observability):** trace captured but its read endpoint + timeline are Phase 2; Phase 1 shows only
  live, ephemeral tool ticks.
- **R7 (dependency fragility):** the `model` health check is **config-only** and never spends tokens
  (no paid call on a health poll).
- **Single-operator:** no auth in Phase 1; FastAPI holds the operator identity. Multi-user/RBAC is M4.
- **Cut lines:** no artifact store, no report download, no trace replay, no E2E harness in Phase 1.

## 11. Open Decisions Folded In

- **Restored sessions are fully interactive** via runtime rehydration (vs read-only history) — safe
  because per-turn agent input is the current message only (§3.4).
- **One in-flight turn per session** enforced by a backend `409 turn_in_progress` guard (vs a queue or
  turn-tagged frames) (§3.5).
- **This spec is M3 Phase 1**, the first slice of roadmap M3 (vs a replacement); Phase 2 completes it.
- Session list backed by a new lightweight `sessions` Mongo collection (vs deriving from
  `thread_counters`) for durable `created_at` + `title` and a clean preview join.
- Single dashboard layout; native `EventSource` for the SSE stream.
- `model` health is config-only (no token spend); deeper probes are explicit opt-in, out of Phase 1.
