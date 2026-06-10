# M2 — Approved Action Workflow (Python/Agent)

> Design spec for the M2 milestone of the e-commerce agent: propose → approve → backend execute,
> with a server-owned conversation thread and a unified per-session event stream.
> Status: Implemented / accepted | Date: 2026-06-09 | Closed: 2026-06-10
> Roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) M2
> Parent design: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md) §2.3, §4.3, §5.2
> Java companion: [../../ecommerce-mcp-server/docs/2026-06-09-m2-execute-companion-design.md](../../ecommerce-mcp-server/docs/2026-06-09-m2-execute-companion-design.md)

## 1. Goal & Scope

Let the agent **propose** business actions that a human approves and the backend executes — the LLM
structurally cannot write. M2 delivers the full vertical loop and demos it through the API + live
event stream. **No visual UI** — the operator console (traces, artifact panels, approval workspace)
is M3; M2 builds the backend + stream infra the console will subscribe to.

**In scope (this repo):**
- `order-manager` sub-agent (reads + `request_approval` only) and **activation of the coordinator**.
- A first-class **session** + server-owned **MongoDB conversation thread**.
- A **unified per-session SSE event stream** (Approach A) carrying both live turn events and durable
  thread appends.
- FastAPI **approval orchestration** (approve → execute) and deterministic **result re-entry**.

**Depends on (separate slice):** the Java companion change — `POST /approvals/{id}/execute`, removed
write `@McpTool`s, extended lifecycle. Land that first.

**Deferred:** visual console + multi-instance stream fan-out (M3/M4); a managed/remote sandbox
executor that drops the `docker.sock` privilege (later `BaseSandbox` swap); batch/delete ops;
skills/memory (parent §4.4 middleware stack lands incrementally).

## 2. Architecture Overview

```
POST /api/sessions/{id}/messages ──► persist user msg ──► spawn agent turn task ──┐
                                                                                  │ publish
GET  /api/sessions/{id}/stream  ◄── SessionBus (in-proc pub/sub, per session) ◄───┤
                                       ▲ token / tool / thread.append             │
POST /api/sessions/{id}/approvals/{aid}/approve ──► Java approve ──► Java execute ─┘ publish
                                                       └─► append approval_status + execution_result

Coordinator (per session)
 ├── sales-analyst   (reads + sandbox + viz)                — unchanged from M1
 └── order-manager   (reads + request_approval; NO writes)  — new in M2
```

Three new subsystems: the **session/thread store** (Mongo), the **SessionBus** (fan-out seam +
unified stream), and the **approval orchestrator**. The agent layer gains the order-manager and
flips the coordinator on.

## 3. Session & Conversation Thread

M1's request-scoped chat API was stateless (`POST /api/chat/stream` with `{message}`, no
persistence, `last_trace` last-writer-wins). M2 makes the **session** first-class and retires that
API.

### 3.1 Session lifecycle & endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /api/sessions` | Create a session, return `{session_id}`. `thread_id == session_id`. |
| `GET /api/sessions/{id}/thread` | Reload the full ordered message list (history on open / fallback). |
| `POST /api/sessions/{id}/messages` | Body `{message}`. Persist the `user` message, spawn the agent turn as a server task, return fast (`202` + message id). Output flows back on the stream. |
| `GET /api/sessions/{id}/stream` | Long-lived SSE — the single channel (see §4). |
| `POST /api/sessions/{id}/approvals/{aid}/approve` | Orchestrate approve → execute (see §6). |
| `POST /api/sessions/{id}/approvals/{aid}/reject` | Body `{reason}`. Reject + append status. |

M1's `POST /api/chat/stream` is **replaced** by this session model (no back-compat shim needed —
single dev consumer today). The M1 live/eval tests that drive `/api/chat/stream`
(`test_chat_stream_live`, `test_hero_live_smoke`, `live_reliability`) **migrate to the session
endpoints in the same slice** — the replacement isn't done until they pass against the new contract.

### 3.2 Conversation thread store (MongoDB)

A `ThreadStore` wraps a `motor` async client. Messages are appended, never mutated; the thread is
the ordered append log.

**Message document (the contract — `GET …/thread` and `thread.append` events return this exact shape):**

```jsonc
{
  "message_id": "uuid",
  "session_id": "uuid",
  "type": "user | agent_answer | agent_proposal | approval_status | execution_result",
  "content": "string (human-readable text)",
  "created_at": "iso-8601",
  "seq": 7,                               // per-session monotonic ordering key (authoritative, NOT created_at)
  "turn_id": "uuid",                      // agent turn that produced this msg (null for human/system appends)
  "trace_id": "uuid",                     // links to the turn's TraceRecord (§3.4)
  "actor_id": "operator user_id",         // who caused it (the agent, or the approving operator)
  "execution_id": null,                   // reserved: links execution_result to a backend execution/audit record
  // type-specific:
  "approval_id":  "...",                  // agent_proposal | approval_status | execution_result
  "card":         { /* server-rendered operation_detail from Java */ },  // agent_proposal
  "tool_name":    "purchase_order_create",                               // agent_proposal
  "status":       "pending|approved|rejected|invalidated|failed|consumed",// agent_proposal|approval_status
  "result":       { /* Java execution_result: po_id, inventory_delta, ... */ }, // execution_result
  "reason":       "string"                // approval_status (rejected)
}
```

`ThreadStore` is a small **async protocol** (prod: `MongoThreadStore` over `motor`; tests: an async
`InMemoryThreadStore`). `append(session_id, msg)`: **Mongo is the source of truth** — persist first,
assigning the next per-session monotonic `seq`, then **best-effort** publish a `thread.append` to the
`SessionBus` (§4). A publish failure never fails the append; live delivery is at-most-once and
`GET …/thread` (ordered by `seq`) is authoritative for recovery. Ordering and dedupe use `seq`, never
`created_at` (which can collide).

### 3.3 Per-session agent & trusted identity

The approval must bind to the **live** session, not the static `spring_mcp_session_id` from settings.
So the agent is built **per session** and cached:

- `SessionRegistry` caches `SessionRuntime{ coordinator, mcp_client, sandbox, created_at, ... }`
  per session.
- The session's Spring MCP connection carries trusted headers `X-Session-Id = session_id`,
  `X-User-Id = <operator>` so `request_approval` binds the approval to this session (Java spec §4.2).
- **Per-session `DockerSandbox`** (not a shared one). The backend already supports this —
  `DockerSandbox(limits, session_id=…)` names its container `ecommerce-sandbox-{session_id}`
  ([sandbox/backend.py](src/ecommerce_agent/sandbox/backend.py)) — so each `SessionRuntime` owns its
  own container and gets true cross-session file isolation, rather than relying on the
  `/workspace`-wide path validation (`_sandbox_file_path`), which is **not** session-scoped today.
- **Idle reaper + concurrency cap:** a thin sweep closes sessions idle past
  `session_idle_ttl_seconds` via the existing `idle_seconds()` / `close()` hooks, and a cap bounds the
  number of live session containers. This is the M2 slice of the per-session model risk R10
  anticipates; a managed/remote executor that drops the `docker.sock` privilege stays a later
  `BaseSandbox` swap. The in-process session map is single-instance only (multi-instance is M4).

### 3.4 Trace capture per session

M1's app-singleton `last_trace` compatibility shortcut remains only for the sequential reliability
harness; the authoritative trace storage is per-session. Each turn's `TraceRecord` is keyed by
`session_id` (+ turn id) in `app.state.trace_records`. Reuses the existing `trace.capture` /
`TraceRecord` machinery unchanged.

## 4. Unified Session Event Stream (Approach A)

A single long-lived SSE per session is the client's one subscription point (M3 console subscribes
once). It multiplexes two layers:

- **Ephemeral turn events** — `token` (incremental answer text), `tool` (tool-call phase). Live UX
  during a turn; not persisted as individual messages. The mapping now lives in
  `sessions.turn._trace_event_to_frame`.
- **Durable `thread.append` events** — the full message doc (§3.2). These are what reload returns;
  the stream merely mirrors the persisted append. Anchoring the event payload to the message schema
  means there is no second contract to revise in M3.

Plus boundary markers: `done` (turn complete, carries `turn_id`) and `error`.

**SessionBus** — in-process per-session pub/sub: `session_id → set[asyncio.Queue]`.
`publish(session_id, event)` fans out; `subscribe(session_id)` is an async generator the SSE
endpoint drains. To avoid a backlog/live race (a message appended between reload and subscribe), the
endpoint **subscribes first** (buffering live events), **then** replays the persisted thread up to
the current `seq`, then emits the buffered live events whose `seq` exceeds the replay cursor. Clients
also dedupe by `seq`. Ephemeral `token`/`tool` events carry no `seq` and are delivered live only — a
mid-turn reconnect replays durable messages, not a partial token stream.

**Turn execution as a server task:** `POST …/messages` persists the `user` message and spawns a task
that runs `coordinator.astream_events`, feeds events through `trace.capture`, and publishes `token`/
`tool` live; on completion it appends the final `agent_answer` or `agent_proposal` message (which
publishes its own `thread.append`) and a `done` marker. The HTTP POST does not stream — it returns
once the turn is enqueued.

## 5. order-manager + Coordinator Activation

### 5.1 order-manager sub-agent

- **Tools:** `purchase_order_query`, `order_query`, `inventory_query`, `supplier_query` (reads) +
  `request_approval` (propose). **No write tools** — structurally enforced by the allowlist.
- `request_approval` moves out of `WRITE_OR_APPROVAL_SPRING_TOOLS` into an order-manager-only allow
  set in [mcp_client.py](src/ecommerce_agent/mcp_client.py); it must **not** be on `sales-analyst`.
  Add `load_order_manager_tools(client)` mirroring `load_spring_read_tools`.
- New `order_manager` prompt in [prompts.yml](src/ecommerce_agent/prompts/prompts.yml): gather facts
  with reads, then call `request_approval`; end the turn with a proposal summary; **never claim the
  action is done** — execution happens after human approval.

### 5.2 Coordinator activation

The dormant seam in [agents.py](src/ecommerce_agent/agents.py) becomes live:

- `build_coordinator(model, *, sales_analyst_subagent, order_manager_subagent, backend)` returns a
  routing deep-agent with `subagents=[sales-analyst, order-manager]` and **no business tools of its
  own** (it delegates; uses the existing `coordinator` prompt at prompts.yml).
- Routing: sales analysis → `sales-analyst`; order/procurement actions → `order-manager`; trivial
  chat → answer directly.
- `_ensure_agent` (chat.py) is replaced by per-session `build_coordinator(...)` (§3.3). The
  coordinator hop is the R2 latency tax we now accept because there is a real routing decision; keep
  the router prompt lean and temperature low.
- Keep the M1 reliability middleware (`ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`).

## 6. Approval Orchestration & Result Re-entry

### 6.1 Proposal → thread

When the order-manager turn calls `request_approval` and ends, the turn task:
1. captures the **raw structured result** of the `request_approval` tool call (the `on_tool_end`
   output *before* trace summarization — `trace.capture` truncates tool output to 500 chars via
   `_summarize` ([trace/capture.py](src/ecommerce_agent/trace/capture.py)), so `approval_id` must
   come from the raw result, **not** `result_summary`) and reads `approval_id` from it;
2. fetches the **server-rendered card** via `GET /approvals/{id}` on Java;
3. appends an `agent_proposal` message (`approval_id`, `card`, `tool_name`, `status=pending`).

A missing/malformed `approval_id` (tool error, schema drift) is a **handled failure**: append an
`agent_answer` surfacing that no proposal was created — never a silent drop (tested, §8). No agent is
suspended; the turn ends normally (parent §5.2).

### 6.2 One human action, two backend transitions

`POST /api/sessions/{sid}/approvals/{aid}/approve` orchestrates the two Java transitions. The
implemented Java scope kept the **shared `X-Service-Token`** auth model (a distinct human/approval
credential is a documented deployment-hardening item, deferred to M4 — Java spec §4.1, parent Java
spec §4.2). So FastAPI calls the Java `/approvals/**` endpoints with `X-Service-Token` plus the acting
user's `X-User-Id`/`X-Session-Id`; for M2 the acting user is the single configured operator `user_id`
(§12), which must match the approval's binding or Java rejects (actor binding). M4 introduces the
distinct authenticated console-user credential.
1. `POST /approvals/{aid}/approve` on Java → flips status to `approved`.
2. `POST /approvals/{aid}/execute` on Java → deterministic backend execute from the stored payload.
3. Append `approval_status(approved)` then `execution_result` messages → published live + persisted.

`…/reject` calls Java `reject` (reason persisted) and appends `approval_status(rejected)`.

The execution result **re-enters the thread with no new LLM turn** — the `execution_result` message
is built deterministically from Java's response.

### 6.3 Idempotency & recovery (risk R8)

- **Bounded retry** on the execute call.
- Java execute is idempotent: an already-`consumed` approval returns the stored `execution_result`;
  the orchestrator surfaces that result (no double effect).
- **Precondition drift** → Java returns `invalidated`; the orchestrator appends an
  `approval_status(invalidated)` message explaining a fresh approval is required.
- **Execution error** (`failed`) → append `approval_status(failed)` with the stored error.
- A stale `approved`-but-unexecuted approval is recovered by re-calling the approve/execute
  orchestration (execute is the recovery path on the Java side).

### 6.4 Settings

Add: `mongo_url`, `approval_api_base_url` (the Spring REST base, e.g. `http://localhost:8080`; the
`/mcp` path is separate), and optional `session_idle_ttl_seconds`. The approval REST calls **reuse the
existing `spring_mcp_service_token`** (shared `X-Service-Token`) — no distinct approval credential in
M2.

## 7. Infrastructure

- **MongoDB is agent-side infra** (the Java server + MySQL stay external per the project setup; Mongo
  is owned/run by this repo). Add a `mongo` service to a dev `docker-compose.yml` and a `mongo_url`
  setting. The Java/MySQL/MCP server is **not** added to this compose (it remains external).
- Driver: `motor` (async). Add to `pyproject.toml` + `uv.lock`.

## 8. Testing

**Default (unit) suite — no external services:**
- `ThreadStore` is a small **async protocol** with two impls: `MongoThreadStore` (motor) for prod and
  an async `InMemoryThreadStore` for tests. The suite uses `InMemoryThreadStore` (not `mongomock`,
  which biases toward sync under an async driver). Cover append/reload, `seq` monotonicity, and the
  best-effort-publish / source-of-truth semantics (§3.2).
- `SessionBus` fan-out: multiple subscribers; **subscribe-first-then-replay** has no gap and no
  duplicates across the `seq` cursor (§4); dedupe by `seq`.
- Approval orchestrator state machine: approve→execute happy path; idempotent replay; `invalidated`;
  `failed`; reject — with the Java REST calls mocked.
- order-manager allowlist test: asserts `request_approval` **present**, the three writes **absent**,
  and `request_approval` **not** on `sales-analyst`.
- Coordinator factory test: the coordinator holds **no business tools** (it only delegates). Routing
  test: order/procurement → order-manager, analysis → sales-analyst.
- Proposal extraction: `approval_id` read from the raw tool result; a missing/malformed id is handled,
  not dropped (§6.1).

**Integration suite (gated, like the sandbox/live suites):** full loop against real Java + MySQL +
Mongo — propose → approve → execute → result re-enters via stream **and** reload; plus negative
cases (precondition drift forces fresh approval; double-spend rejected; reject path). Gate behind an
env flag and skip clearly when services are absent.

Implementation note: the gated live loop asserts the durable reload contract against real
Java/MySQL/Mongo. The live stream publish path is covered with direct `SessionBus` assertions in the
default suite to avoid brittle long-lived SSE timing in the external-service test.

## 9. Acceptance (mirrors roadmap M2)

- Agent can propose a purchase order but holds **no tool capable of executing it**. `GET /health/mcp`
  reports **per-agent allowlists** (reads + `get_statistics` shared; `request_approval` on
  order-manager only; the three writes on neither), and the allowlist unit test asserts the same.
- An approval can be approved/rejected via the API; Java `approve` only flips status while FastAPI
  orchestrates the separate approve + execute transitions as one human action.
- The execute call is retried / idempotently reported.
- Execution completion appends a **deterministic** `execution_result` message; it survives reload
  **and** pushes live over the session stream.
- A changed live DB precondition between approval and execution forces a **fresh approval**
  (`invalidated`).
- One approval cannot be replayed or double-spent.

## 10. Build Sequence

1. **Java slice first** — companion spec §9 (execute endpoint, remove write `@McpTool`s, lifecycle,
   negative matrix green).
2. **Session + thread store + SessionBus + unified stream** — sessions, Mongo `ThreadStore`,
   `GET …/thread`, `GET …/stream`, `POST …/messages` (turn-as-task), per-session trace, and the
   per-session `DockerSandbox` + idle reaper/concurrency cap.
3. **order-manager + coordinator activation** — allowlists, `order_manager` prompt,
   `build_coordinator`, replace `_ensure_agent`.
4. **Approval orchestration + result re-entry** — proposal message, approve/reject/execute
   orchestration, idempotency/recovery, credential separation.
5. **Integration loop** — gated end-to-end test against real Java + MySQL + Mongo.

## 11. Risks Touched & Cut Lines

- **R1 (scope/throughput):** WIP = 1; M2 stops at the loop + stream, no console.
- **R2 (latency):** coordinator hop accepted now (real routing decision); lean router prompt.
- **R8 (approve↔execute limbo / two-turn coherence):** idempotent execute + bounded retry + durable
  thread re-entry (§6.3).
- **R13 (two-repo coordination):** Java change is its own reviewed slice with the negative matrix
  re-run; specs cross-linked.
- **R10 (sandbox isolation):** M2 moves to per-session containers (true isolation) + a thin idle
  reaper/concurrency cap (§3.3); the `docker.sock`-privilege removal stays a later `BaseSandbox` swap.
- **R12 (audit/artifact schema):** the message schema carries the audit spine now — `seq`, `turn_id`,
  `trace_id`, `actor_id`, and a reserved `execution_id` (§3.2).
- **Cut lines:** no batch/delete; no `interrupt()`/resume; no generic messaging platform; no visual
  UI; multi-instance stream fan-out deferred to M3/M4.

## 12. Best-Guess Decisions To Confirm At Review

- Session created via explicit `POST /api/sessions` (vs. lazy-create on first message).
- Operator identity is a single configured `user_id` for M2; real multi-user RBAC is M4.
- Mongo runs via a dev `docker-compose` in this repo (vs. an externally managed Mongo like the Java
  server).
- `motor` as the async Mongo driver; unit tests use an async `InMemoryThreadStore` behind the
  `ThreadStore` protocol (not `mongomock`).
