# M3 — Operator Console (MVP) Design

> Design spec for the M3 milestone: a single-operator web console that makes the agent's work
> visible — conversation, live streaming, and the human approval workflow built in M2.
> Status: Draft | Date: 2026-06-10
> Roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) M3
> Consumes: [2026-06-09-m2-approved-action-workflow-design.md](2026-06-09-m2-approved-action-workflow-design.md)
> Parent design: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)

## 1. Goal & Scope

Turn the system into a usable work surface, not just an API: an operator can hold a conversation,
watch the agent stream its answer and tool activity, and approve/reject proposed business actions
with the impact in front of them — all over the M2 contract.

**MVP scope (core loop first — R1):**
- **Session list + conversation view** (reload + live stream).
- **Streaming answer panel** (token-by-token, live tool activity).
- **Approval workspace** (server-rendered card/impact, one-click Approve → execute, Reject + reason,
  live status, execution result).
- **Health panel** (MCP servers, sandbox, model, Mongo).

**Stack:** React + TypeScript + Vite, built to static assets and served by FastAPI. Single-operator —
no login (the operator identity stays in FastAPI settings; multi-user auth/RBAC is M4).

**Deferred to M3 Phase 2 / later:** tool-trace timeline (needs a trace-read endpoint), artifact/chart
panel + report download, broad theming/polish, Playwright E2E. Multi-user auth → M4.

## 2. Architecture & Delivery

A **single-page React app** (Vite). The JSON/SSE API stays under `/api` and `/health`; FastAPI serves
the built SPA via `StaticFiles` at `/` with a catch-all so a client-side refresh returns `index.html`.
In dev, the Vite dev server proxies `/api` + `/health` to FastAPI, so there is one origin and no CORS
configuration.

**Layout — a single multi-panel dashboard, not routed pages:** a left **session sidebar**, a center
**conversation/stream**, and a right **approval + health rail**. This is the operator-console shape and
the most demoable; routed list→detail pages were rejected as navigation overhead with no MVP benefit.

**Data flow:** the SPA is a thin consumer of the M2 contract — `GET …/thread` for history, the SSE
`…/stream` for live `token`/`tool`/`done`/`error` + durable `thread.append`, `POST …/messages` to
send, and `POST …/approvals/{id}/approve|reject`. Approval/execution results re-enter as
`thread.append`s on the same stream — no extra channel. State: React Query for fetches plus a small
reducer for the live, SSE-driven message list (dedupe by `seq`).

## 3. Backend Additions

M3 is **not pure frontend** — the trace exists but is unexposed, and there is no way to list sessions.
Three small FastAPI additions; everything else already exists in [api/sessions.py](src/ecommerce_agent/api/sessions.py).

1. **Durable session records.** On `POST /api/sessions`, write a lightweight `sessions` Mongo doc
   `{session_id, created_at, title}` (`title` defaults to null, set from the first user message). This
   makes the session list survive restarts — the in-memory `SessionRegistry` is reaped — reusing the
   existing Mongo connection in [threads/mongo.py](src/ecommerce_agent/threads/mongo.py).
2. **`GET /api/sessions`** — list sessions newest-first with `{session_id, title, created_at,
   last_message_preview, message_count}` for the sidebar.
3. **`GET /api/sessions/{id}`** — minimal metadata (`title`, `created_at`, `message_count`) for the
   conversation header.
4. **Static serving** — mount `frontend/dist`; a catch-all route returns `index.html` for non-`/api`,
   non-`/health` paths so client-side state survives refresh.

The `title` is derived once, from the first `user` message of a session (truncated). Listing reads
from the `sessions` collection joined with the latest `thread_messages` entry for the preview.

**Explicitly deferred (Phase 2):** a `GET /api/sessions/{id}/turns/{turn_id}/trace` endpoint — the data
already sits in `app.state.trace_records` — for the tool-trace timeline; and any artifact store for the
chart/report panel.

## 4. Frontend Surfaces & State

Components under the 3-pane shell:

- **SessionSidebar** — `GET /api/sessions`; "New session" (`POST /api/sessions`); selects the active
  session. Shows title + last-message preview.
- **ConversationView** (center) — renders the thread by message `type` (`user`, `agent_answer`,
  `agent_proposal`, `approval_status`, `execution_result`) and a composer (textarea → `POST …/messages`).
- **ApprovalWorkspace** (right rail) — pending proposals as cards (§5).
- **HealthPanel** (right rail, collapsible) — polls `/health` + `/health/mcp`; status dots for Spring
  MCP / ModelScope / sandbox / model / Mongo.
- **StreamProvider / useSessionStream** — owns one `EventSource` per active session.

**SSE handling (core mechanic):** opening a session opens the stream, which **replays the thread
backlog as `thread.append` then goes live** (the M2 stream already does this), so the message list is
built purely from `thread.append` upserts keyed by `seq` (idempotent dedupe). `GET …/thread` is the
reload fallback. The live `token`/`tool` frames are **not** turn-tagged (only `done` carries
`turn_id`), so the frontend tracks the single in-flight turn from the `POST …/messages` response
(`{turn_id, user_message_id}`) — there is one in-flight turn per session. `token` frames accumulate
into a **provisional answer bubble** for that turn; when the turn's durable `agent_answer` arrives as a
`thread.append` (it carries the `turn_id`), it replaces the provisional bubble. `tool` frames drive a
transient "using `inventory_query`…" indicator; `done` (with `turn_id`) finalizes the turn; `error`
shows a non-blocking toast (the durable failure `agent_answer` with `status="failed"` still arrives via
`thread.append`).

**State shape:** per active session — a `Map<seq, ThreadMessage>` (the rendered list), an in-flight
token buffer keyed by `turn_id`, and approvals derived by grouping messages on `approval_id`. TS types
for `ThreadMessage` and the SSE event envelopes live in one `src/types` module that mirrors the Python
schema in [threads/messages.py](src/ecommerce_agent/threads/messages.py).

## 5. Approval Workspace UX

An `agent_proposal` message carries `approval_id`, the server-rendered `card`, `tool_name`, and
`status="pending"`. The workspace renders each as a card: operation summary + the `card`'s impact/diff
fields + a status badge.

**One human action.** An **Approve** button calls `POST …/approvals/{id}/approve` (FastAPI orchestrates
approve→execute server-side — one click, two auditable backend transitions). A **Reject** button takes
an optional reason and calls `POST …/approvals/{id}/reject`.

**Live lifecycle off the same stream.** The approve/reject endpoints append `approval_status` and
`execution_result` messages, which arrive as `thread.append`s. The workspace folds an approval's
messages on `approval_id` to compute current state:

- `pending → approved → consumed` — show the execution result from `result` (e.g. "PO #123 placed,
  inventory +500").
- `rejected` — show the reason.
- `invalidated` — "preconditions changed — request a fresh approval."
- `failed` — show the error from the status message.

Buttons disable while a request is in flight and once status is non-`pending`; a `409` (already decided)
reconciles to server state instead of erroring. Multiple pending approvals each render their own card,
and proposals also appear inline in the conversation as proposal bubbles.

## 6. Error Handling

- GETs use React Query retry; mutations (send / approve / reject) surface inline errors and allow retry.
- `EventSource` auto-reconnects; because the stream replays backlog on connect, reconnect re-syncs state
  (dedupe by `seq`). Show a subtle "reconnecting" hint.
- `404` (session reaped/unknown) refreshes the list and clears the active session.
- Approval `409` reconciles to server state; other approval errors surface the Java error payload
  (FastAPI already passes it through as the `HTTPException` detail) and leave the card in its last known
  state.
- Degraded `/health/mcp` shows red dots without blocking use.
- Every panel has explicit loading and empty states.

## 7. Testing

- **Frontend (Vitest + React Testing Library):** a mocked API/SSE helper. Key cases: `seq` dedupe;
  provisional→durable answer swap by `turn_id`; approval status folding
  (`pending→consumed|rejected|invalidated|failed`); session switch tears down and re-opens the right
  `EventSource`; health rendering; error/empty states.
- **Backend (pytest, existing conventions):** `GET /api/sessions` (newest-first ordering, preview,
  title-from-first-message), the `sessions` doc written on `POST /api/sessions`, `GET /api/sessions/{id}`,
  and a static-serving catch-all smoke. Extend the existing fake-Mongo test doubles for the `sessions`
  collection.
- Playwright E2E is deferred to Phase 2; MVP relies on component + backend tests plus a manual demo.

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
  api/sessions.py             # + GET /api/sessions, GET /api/sessions/{id}
  api/app.py                  # + StaticFiles mount + SPA catch-all
  threads/mongo.py            # + sessions collection (create/list/get) + title
```

Frontend files stay small and single-responsibility; the per-session reducer is the one piece of real
logic and is unit-tested in isolation. The build (`frontend/dist`) is produced by Vite and mounted by
FastAPI; a make/uv script wires `npm run build` into the app's serve path for prod.

## 9. Acceptance (MVP)

- Operator can create and select sessions from the sidebar; the list survives a server restart.
- Sending a message streams the answer token-by-token with a live tool-activity indicator.
- A proposal renders with the server-rendered impact; **Approve** runs approve→execute and the execution
  result appears **live and survives reload**; **Reject** with a reason shows `rejected`.
- `invalidated` and `failed` render correctly; an approval cannot be double-acted from the UI.
- The health panel reflects Spring MCP / ModelScope / sandbox / model / Mongo status.
- A browser refresh rebuilds state from `thread` + stream; a dropped stream reconnects and re-syncs.

## 10. Risks Touched & Cut Lines

- **R1 (scope/throughput):** core loop only; tool-trace timeline + artifact/chart panel are an explicit
  Phase 2; no theming/polish until the core surfaces work (roadmap M3 cut line).
- **R5 (observability):** the trace is captured but its read endpoint + timeline are Phase 2; MVP shows
  only live, ephemeral tool ticks.
- **Single-operator:** no auth in M3; FastAPI holds the operator identity. Multi-user/RBAC is M4.
- **Cut lines:** no artifact store, no report download, no E2E harness, no trace replay in MVP.

## 11. Open Decisions Folded In (no blocker)

- Session list is backed by a new lightweight `sessions` Mongo collection (vs deriving from
  `thread_counters`) — chosen for durable `created_at` + `title` and a clean preview join.
- Single dashboard layout (vs routed pages).
- Native `EventSource` for the SSE stream (vs `fetch`-stream) — standard for a `GET` SSE endpoint.
