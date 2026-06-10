# M3 Codebase Review

**Date:** 2026-06-10
**Scope:** Full codebase after M3 Phase 1 completion (backend + frontend)
**Commits reviewed:** `ba58b6e..5b14d30` (M3 backend + frontend + polish fixes)
**Test status:** 150 pytest passed / 8 skipped, 38 Vitest passed, ruff clean, 0 npm vulnerabilities

---

## Executive Summary

The codebase is well-structured with clear separation of concerns (sessions, threads, sandbox, approvals, trace). The M1–M3 milestone architecture is coherent. The review identified **7 critical, 21 important, and 17 minor issues** across backend, frontend, tests, and infrastructure. The most impactful themes are: event-loop blocking from synchronous Docker calls, unbounded memory growth in several paths, and missing auth (acknowledged M4).

---

## Critical Issues

### C1. Docker ops block the event loop

**Location:** `src/ecommerce_agent/sessions/registry.py:115-127`, `src/ecommerce_agent/sandbox/backend.py:92-103`

`runtime.close()` calls `DockerSandbox.close()` which calls `container.remove(force=True)` — a synchronous Docker API call that can block for seconds. This is called inside `asyncio.Lock` in `_reap_idle_locked`, `_make_room_locked`, and `close_all`, stalling the entire event loop during container removal.

**Risk:** SSE timeouts, request pile-up, perceived unresponsiveness during reaping or shutdown.

**Fix:** Wrap all Docker calls in `asyncio.to_thread()` (or migrate to `aiodocker`).

---

### C2. No authentication on any endpoint

**Location:** `src/ecommerce_agent/api/sessions.py`, `src/ecommerce_agent/api/app.py`

Every route — session creation, message posting, approval approve/reject, thread reads — is completely unauthenticated. The `X-Service-Token` and `X-User-Id` headers sent to Java are static config values, not derived from caller identity. Any network-reachable client has full operator privileges.

**Risk:** Any network client can approve purchase orders.

**Status:** Acknowledged as M4 scope. Should be prioritized before any non-local deployment.

---

### C3. `/health` leaks internal exception details

**Location:** `src/ecommerce_agent/api/health.py:13,25`

Error responses include full exception text: `f"{type(exc).__name__}: {exc}"`. This can expose MongoDB connection strings, file paths, and Docker daemon socket paths to unauthenticated callers.

**Risk:** Information disclosure to any caller hitting `/health`.

**Fix:** Replace with generic error messages; log the full exception server-side instead.

---

### C4. `_build_runtime` failure can leave unclosed runtime references

**Location:** `src/ecommerce_agent/sessions/registry.py:73-96`

If `_build_runtime(session_id)` raises (e.g., MCP connection failure), the partially-built `SessionRuntime` object may hold an open `DockerSandbox` client or other resources that are never cleaned up. Note: `DockerSandbox.__init__` does not start a container — containers are created lazily in `_ensure_container()` during `execute`/`upload` — so this is a runtime/client cleanup concern, not a container leak.

**Risk:** Leaked Docker client objects and other resources per failed rehydration.

**Fix:** Add try/except inside `_build_runtime` that closes the runtime on failure, ensuring any initialized clients are released.

---

### C5. `trace_records` unbounded in-memory growth

**Location:** `src/ecommerce_agent/api/app.py:274`

`app.state.trace_records` is a plain dict that grows via `trace_records.setdefault(session_id, {})[turn_id] = record` with no eviction. Every turn's `TraceRecord` (including model timing, token usage, tool calls) accumulates forever.

**Risk:** OOM in long-running processes.

**Fix:** Cap per-session entries (e.g., last N turns) or flush to disk periodically.

---

### C6. `SessionBus` queues unbounded

**Location:** `src/ecommerce_agent/sessions/bus.py:13`

`asyncio.Queue()` has no `maxsize`. If an SSE client is slow to consume (e.g., backgrounded browser tab), the queue grows without bound. Long agent turns can produce hundreds of events.

**Risk:** Memory growth per slow/stuck SSE client; OOM with many concurrent sessions.

**Fix:** Set `maxsize` on the queue and drop oldest events or apply backpressure when full.

---

### C7. Frontend error event doesn't finalize in-flight turn

**Location:** `frontend/src/state/sessionReducer.ts:77-78`

When the backend sends an `error` SSE event mid-turn, the reducer only sets `state.error` but leaves `inFlightTurnId`, `tokenBuffer`, and `activeTool` untouched. In practice, `run_turn` appends a `failed` `agent_answer` then publishes `done` after the `error`, so the turn does finalize via the `done` event. However, the reducer should still handle the `error` event defensively — if the `done` event is lost or delayed, `inFlightTurnId` stays set.

**Risk:** Momentary composer delay; recovers when `done` arrives. Still worth fixing for robustness.

**Fix:** Clear `inFlightTurnId`, `tokenBuffer`, and `activeTool` on error using the existing `finalize` helper:
```ts
case "error":
  return { ...finalize(state), error: action.message };
```

---

## Important Issues

### Backend — Resource & Correctness

| # | Issue | Location | Detail |
|---|-------|----------|--------|
| I1 | `MongoThreadStore.append` not atomic | `threads/mongo.py:31-39` | Counter increment and document insert are separate operations; crash between them creates seq gaps. The frontend dedupe expects monotonic seq. |
| I2 | `list_sessions` O(N) with per-session queries | `api/sessions.py:176-191` | For every session record, makes two MongoDB queries (`latest_message`, `count_messages`). No pagination. Degrades linearly with session count. |
| I3 | Approval clients cached, never evicted | `api/sessions.py:33-44` | `ApprovalClient` (and its `httpx.AsyncClient`) cached per session in `app.state.approval_clients`. Reaped sessions' clients remain open until shutdown. |
| I4 | Runtime created before durable record | `api/sessions.py:169-173` | If `session_registry.create()` succeeds but `session_store.create()` fails (e.g., MongoDB down), an in-memory runtime exists with no durable record. Note: no Docker container is leaked — containers are created lazily on `execute`/`upload`. Consider a create-then-mark-ready pattern: persist a `creating` record, build the runtime, then update the record to `ready` with rollback on failure. |
| I5 | Background task exceptions silently swallowed | `api/sessions.py:261-283` | If `run_turn` raises something unexpected that escapes its handler, the exception goes to the task's future but is never retrieved or logged. Operator sees no answer and no error. |
| I6 | `_needs_order_manager` keyword matching too broad | `sessions/factory.py:73-75` | "what is the approval process?" routes to coordinator because it contains "approval". "what does order status mean?" routes to order manager. |
| I7 | Two separate MongoDB connection pools | `sessions/store.py:94-97`, `threads/mongo.py:21-24` | `MongoSessionStore` and `MongoThreadStore` each create their own `AsyncIOMotorClient`, doubling connection count. |
| I8 | No request body size limit | `api/sessions.py:22` | `MessageRequest.message` has `min_length=1` but no `max_length`. Arbitrarily large messages get persisted and fed to the LLM. |
| I9 | `end_turn` failure leaves session permanently stuck | `api/sessions.py:277-278` | `registry.end_turn(session_id)` runs in a `finally` block. If it raises, the turn marker is never released and no subsequent messages are accepted. |
| I10 | Weak default secrets | `config.py:30` | `spring_mcp_service_token` defaults to `"dev-service-token"`. Production silently uses this if env var is unset. |
| I11 | SSE dedupe crashes on malformed `thread.append` events | `api/sessions.py:400` | The cursor-based dedupe accesses `event["message"]["seq"]`. The `and` short-circuit protects against non-`thread.append` events, but if a `thread.append`-typed event is published without a `message` key, it crashes the SSE generator for that subscriber. Defensive hardening, not a production failure under correct internal usage. |
| I12 | SSE messages cast without runtime validation | `frontend/src/api/streamEvents.ts:17` | `body.message as ThreadMessage` with only a `typeof body.message === "object"` check. If the backend sends a partial message, downstream consumers operate on undefined fields. Defensive hardening — the backend controls all event shapes. |

### Frontend — Performance & UX

| # | Issue | Location | Detail |
|---|-------|----------|--------|
| I13 | `sortedMessages` O(N log N) on every append | `sessionReducer.ts:31-33` | Full re-sort triggered on every `thread.append`. Degrades with long conversations. |
| I14 | No virtualization or pagination for messages | `sessionReducer.ts:44-46` | Unbounded `bySeq` map grows with every message. Combined with per-message rendering, this degrades significantly over time. |
| I15 | Token streaming triggers `scrollIntoView` on every token | `ConversationView.tsx:89-91` | Layout thrashing during fast streaming (dozens of calls per second). |
| I16 | No loading indicators for initial data fetches | Multiple components | `sessionsQuery.isPending` / `healthQuery.isPending` never surfaced. Users see empty state that abruptly fills in. |
| I17 | No `aria-live` for dynamic status/error messages | `ConversationView.tsx`, `ApprovalWorkspace.tsx` | Screen readers don't announce error notices, busy notes, or reconnection status. |

### Infrastructure

| # | Issue | Location | Detail |
|---|-------|----------|--------|
| I18 | `docker-compose.yml` missing healthchecks, restart, limits | `docker-compose.yml` | No `healthcheck` on mongo, no `restart: unless-stopped`, no resource limits. |
| I19 | Sandbox containers not auto-removed | `sandbox/backend.py:87-90` | `auto_remove: False` — process crash leaves orphaned containers. Needs startup sweep. |
| I20 | No CORS middleware for split deployments | `app.py:164` | No `CORSMiddleware` configured. Not a concern for same-origin FastAPI-served SPA; only matters when frontend and backend are deployed on separate origins. Separate concern from auth. |
| I21 | Inconsistent error response shapes | `sessions.py` (multiple) | 409 returns `{detail: {error: ...}}`, 404 returns plain string, approval errors return `_public_payload`. Clients cannot parse uniformly. |

---

## Minor Issues

### Backend

| # | Issue | Location |
|---|-------|----------|
| M1 | `_public_payload` strips `_` keys but `approval_card` doesn't strip `_http_status_code` | `sessions.py:47-48`, `approvals.py:113` |
| M2 | `get_settings` uses `lru_cache` with no invalidation | `config.py:57-59` |
| M3 | `_summarize` in trace capture uses `repr()` on large objects before truncation | `trace/capture.py:17-19` |
| M4 | No MongoDB index management | `sessions/store.py`, `threads/mongo.py` |
| M5 | `_reap_loop` interval hardcoded to 60s | `app.py:89` |
| M6 | Module-level `app = create_app()` has import-time side effects | `app.py:207` |
| M7 | `_approval_client` fallback path creates unclosed `httpx.AsyncClient` | `sessions.py:44` |
| M8 | `extract_approval_id` recursive descent on arbitrary dicts — stack overflow risk | `approvals.py:135-168` |

### Frontend

| # | Issue | Location |
|---|-------|----------|
| M9 | `foldApprovals` re-sorts already-sorted input | `approvals.ts:15` |
| M10 | Dynamic CSS class from unsanitized `status` field | `ConversationView.tsx:134` |
| M11 | `cardEntries` called twice per approval card | `ApprovalWorkspace.tsx:63,65` |
| M12 | No keyboard shortcut for sending (Ctrl+Enter) | `ConversationView.tsx:93-99` |
| M13 | No focus management after session switch or error | `App.tsx:187-192` |
| M14 | `imageArtifacts` re-parses on every render | `ConversationView.tsx:129` |
| M15 | Production CORS not documented | `vite.config.ts` (dev proxy only) |

---

## Test Coverage Assessment

### Strong Coverage (>80%)

- `api/sessions.py` — 404s, 409s, SSE streaming, approvals, session lifecycle
- `sessions/registry.py` — concurrency, rehydration, eviction, turn guard
- `sessions/turn.py` — failure paths, approvals, chart artifacts
- `config.py` — defaults, frontend_dist_dir
- `sandbox/config.py` — container security settings
- `trace/` — capture, JSONL, schema

### Critical Gaps

| Module | Coverage | Key Gap |
|--------|----------|---------|
| `sandbox/backend.py` | ~10% unit | No unit tests for `_sandbox_file_path` (path traversal is security-critical). All tests require Docker. |
| `approvals.py:ApprovalClient` | ~0% | Real HTTP client never tested; only fakes used. |
| `approvals.py:extract_approval_id` | ~20% | Only 1 of 5+ paths tested (dict, list, attribute, regex, None). |
| `models.py` | ~20% | Only temperature tested. Model name, base_url, api_key wiring untested. |
| `registry.py:close_all` | 0% | Teardown path never tested. |
| `SessionSidebar` | 0% | No dedicated component test. |
| `ApprovalWorkspace` | 0% | No dedicated component test. |

### Reliability Risks

| Test | Risk |
|------|------|
| `test_app.py:wait_for_thread_types` | Polls with 2s timeout; may flake under CI load |
| `test_sessions_api.py:_wait_for_thread` | Same polling pattern, same 2s timeout |
| `test_chat_stream_live.py:_wait_for_agent_answer` | 120s poll; hangs CI for 2 minutes on failure |
| `test_hero_live_smoke.py:_fail_after` | Uses `signal.SIGALRM` — silently becomes no-op in non-main thread |

---

## Recommended Priorities

### Immediate (before next deployment)

1. **Fix C1** — Wrap Docker calls in `asyncio.to_thread()` to stop blocking the event loop
2. **Fix C3** — Sanitize `/health` error messages (don't expose exception text)
3. **Fix C5/C6** — Bound `trace_records` size and `SessionBus` queue `maxsize`
4. **Fix C7** — Reducer error finalization (1-line change, `finalize(state)` on error)
5. **Fix I11** — Add defensive type guard on SSE dedupe before accessing `event["message"]`

### Near-term (before scale / multi-operator use)

6. **Fix I1** — MongoDB transaction for counter+insert, or document seq-gap tolerance
7. **Fix I2** — Pagination + aggregation for `list_sessions`
8. **Fix I4** — Create-then-mark-ready pattern: persist a `creating` record, build runtime, then update to `ready` with cleanup on failure
9. **Fix I13/I14** — Incremental sort or defer to consumer; add message virtualization
10. **Fix I3** — Evict approval clients when sessions are reaped

### Strategic (M4+)

11. Auth (acknowledged as M4; deployment-blocking before non-local use)
12. CORS configuration (deployment-shape concern for split frontend/backend; separate from auth)
13. Sandbox orphan cleanup (startup sweep + `auto_remove`)
14. Shared MongoDB client across stores
15. Add unit tests for `sandbox/backend.py` and `approvals.py:ApprovalClient`
16. Extract shared test fakes to `conftest.py`

---

## Positive Observations

- **Sandbox isolation is well-hardened**: `network_mode: none`, `read_only: True`, `cap_drop: ALL`, `no-new-privileges`, unprivileged user, tmpfs-only writable paths, path traversal validation.
- **Frontend architecture is clean**: Reducer/stream separation, ref-based stale-closure prevention, approval folding pattern, 409 reconciliation.
- **Approval state machine is thorough**: pending → approved → executing → consumed/rejected/invalidated/failed with replay protection.
- **Test suite is comprehensive**: 150 backend + 38 frontend tests covering happy paths, error paths, concurrency, and SSE lifecycle.
- **Design docs are detailed and up-to-date**: Each milestone has a spec and implementation plan, with review notes captured.
