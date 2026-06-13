# M4 Slice 5 — Identity, Session Isolation, RBAC & Audit (Design)

> Status: Draft | Date: 2026-06-13
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) (§M4 — strong session
> isolation + role-based permissions; audit search and retention policy)
> Predecessors: [Slice 1 — Routing Eval](2026-06-11-m4-routing-eval-design.md),
> [Slice 2 — Conversation Memory](2026-06-12-m4-slice2-conversation-memory-design.md),
> [Slice 3 — Eval Expansion](2026-06-12-m4-slice3-eval-expansion-design.md),
> [Slice 4 — Tool-Choice Eval](2026-06-12-m4-slice4-tool-choice-eval-design.md) (all complete)
> Cross-repo: [ecommerce-mcp-server spec](../../ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md),
> [M2 execute companion](../../ecommerce-mcp-server/docs/2026-06-09-m2-execute-companion-design.md)

## 1. Context & Goal

Slices 1–4 built the eval correctness arc (routing, memory, approval-safety, tool-choice). This slice
**pivots from eval to product hardening** and bundles two M4 capabilities the roadmap kept deferring —
**strong session isolation + role-based permissions** and **audit search + retention** — because they
share one spine: an **actor identity**. Identity drives session ownership (isolation), role drives who
may propose/approve/execute operational changes, and the same `actor_id`
stamped on every action is what audit search filters on. Bundling them means one auth/actor model
serves all three, and one spec/one review.

**Standard.** This is built to a real multi-operator standard, not demo-grade: authenticated (verified,
not asserted) identity, hashed passwords, revocable sessions, per-actor binding enforced end-to-end.
Implementations stay lightweight; corners are not cut. Where "lightweight" and "faithful" diverge, we
take faithful (e.g. a verified HttpOnly session cookie, never an unverified asserted header).

**This slice changes runtime behavior** (auth gates, isolation, actor wiring) — unlike the eval-only
slices. It also makes a small cross-repo change (§8).

## 2. Architecture — three trust boundaries

The system is already a clean gateway/BFF architecture. This slice completes the missing edge.

1. **Browser ↔ FastAPI** — *the new layer.* Today every endpoint is open. Add HttpOnly session-cookie
   authentication; resolve a request `Actor` from the cookie. The SPA gets a minimal auth shell in this
   slice (`/me` bootstrap, login, logout, 401 handling) so the existing console remains usable once the
   API gates turn on.
2. **FastAPI ↔ Spring** — service token + the authenticated user's `spring_user_id` sent as `X-User-Id`
   on **every** Spring path: MCP tools inside the agent runtime and approval REST calls.
   **Already built and enforced**: `TrustedActorFilter` validates the service token (constant-time) and
   binds the asserted user id; `ApprovalController`/`ApprovalService` own approvals by `(userId,
   sessionId)` and deny cross-actor access via `isSameActor`. This slice sends the *real* per-actor id
   instead of the fixed `spring_mcp_user_id="1"`.
3. **LLM ↔ writes** — strengthened by role-shaped runtime. The LLM still holds no direct write tools;
   approval proposal creation goes through `request_approval`, and only roles with `PROPOSE` get an
   order-manager runtime/tool surface capable of calling it. Approve/reject/execute remain separately
   gated human actions.

## 3. Identity & auth — new `auth/` module

### 3.1 Models — `auth/models.py`
- `Role(StrEnum)` = `{viewer, operator}`. Extensible: `admin`/`analyst` add cleanly later (§3.5).
- `User`: `user_id`, `username`, `password_hash`, `role`, `spring_user_id` (`int`/`Long`), `created_at`.
- `Actor`: the resolved request principal — `user_id`, `username`, `role`, `spring_user_id`. Carries no
  secret; built per request from the login session.

### 3.2 Passwords — `auth/passwords.py`
`hash_password` / `verify_password` via `passlib` (argon2). Login failures are generic ("invalid
credentials") with no user enumeration; verification is constant-time (passlib).

### 3.3 User store — `auth/users_store.py`
Mongo `users` collection: `get_by_username`, `get_by_id`, `create`. Unique index on `username`. An
`InMemoryUserStore` test double mirrors the protocol. Users are **seeded via a CLI command** (§9), so
adding operators needs no redeploy.

### 3.4 Login-session store — `auth/login_sessions.py`
Mongo `auth_sessions` collection: `create(user_id) -> session_id`, `get(session_id) -> record|None`,
`delete(session_id)`. Records hold `{_id: opaque, user_id, created_at, expire_at}`. Opaque ids are
cryptographically random (`secrets.token_urlsafe`). Revocable on logout. Named **login session** to
avoid clashing with the existing conversation-`session` concept. An in-memory double mirrors it.
A TTL index on `expire_at` expires stale logins (`auth_session_ttl_seconds`).

### 3.5 Permissions — `auth/permissions.py`
`Action(StrEnum)` (`PROPOSE`, `APPROVE`, `AUDIT_SEARCH`, …) and a single
`can(role: Role, action: Action) -> bool`
map. **All authorization decisions route through `can()`** — no scattered `if role == ...` checks, so a
new role is one map entry and never touches call sites. Current map:

| action | viewer | operator |
|---|---|---|
| PROPOSE (order-manager / `request_approval` proposal creation) | ✗ | ✓ |
| APPROVE (approve/reject/execute) | ✗ | ✓ |
| AUDIT_SEARCH (cross-session) | ✗ | ✓ |

(Reading/chatting in one's *own* sessions is allowed to any authenticated actor; it is gated by
ownership in §4, not by `can()`. For viewers, a write-intent chat turn is allowed to reach the router,
but the selected write specialist is denied by policy before any `request_approval` tool can run.)

### 3.6 Dependencies & router — `auth/dependencies.py`, `api/auth.py`
- `current_actor` dependency: read the cookie → resolve login session → load user → build `Actor`;
  **401** if the cookie is missing, unknown, or expired.
- `require(action)` dependency factory: **403** if `can(actor.role, action)` is false.
- `POST /api/auth/login`: verify username + password; create a login session; set
  `Set-Cookie: <name>=<id>; HttpOnly; SameSite=Lax; Path=/` and include the `Secure` attribute when
  `auth_cookie_secure` is true. Returns the public actor (`/me` shape). **401** on bad credentials.
  `auth_cookie_secure` is config-driven so local HTTP/dev tests can use cookies; production sets it true.
- `POST /api/auth/logout`: delete the login session row and clear the cookie.
- `GET /api/auth/me`: return the current `Actor` (or 401).

### 3.7 Frontend auth shell
This slice includes the minimum frontend needed to keep the operator console usable after auth gates land:
- On boot, call `GET /api/auth/me`; if 401, show a login form instead of the session console.
- Login posts username/password to `/api/auth/login`; successful login stores no token in JS (cookie only)
  and then loads the normal session UI.
- Logout calls `/api/auth/logout`, clears React Query/session state, and returns to the login form.
- Existing `fetch`/`EventSource` same-origin calls automatically carry the HttpOnly cookie. API helpers
  surface 401 so the app can return to login if the server-side login session is revoked/expired.

No audit-search UI ships in this slice; the audit endpoint is API-only until a later console panel.

## 4. Session isolation

The conversation `SessionStore` gains an `owner_id`:
- `create(session_id, owner_id)` stamps the authenticated actor's `user_id`.
- `list_records(owner_id=...)` filters to the caller's sessions.
- `get` returns the record incl. `owner_id`.

Every conversation endpoint (`GET /api/sessions`, `GET /{id}`, `/{id}/thread`, `/{id}/artifacts`,
`/{id}/turns/{tid}/trace[/export]`, `POST /{id}/messages`, `/{id}/stream`, the approve/reject routes)
requires `current_actor` and returns **403** (or **404** to avoid existence disclosure) unless
`owner_id == actor.user_id`. **This applies to all roles** — operators do not get cross-session
visibility through the conversation surface. The *only* cross-session read path is the operator audit
API (§6). This keeps conversation endpoints strictly private and makes the audit API the single,
deliberate cross-cutting view.

`SessionRegistry.create(actor)` (which mints the id today) threads `owner_id` to the store and builds a
runtime with that actor's `spring_user_id`. Existing-session runtime rebuilds are also actor-bound:
`get_or_create_runtime(session_id, actor, session_known)` runs only after the endpoint has confirmed
`owner_id == actor.user_id`, and it rebuilds with `actor.spring_user_id`. Cached runtimes carry
`owner_id`/`spring_user_id`; if a cached runtime's owner does not match the request actor, the registry
raises instead of reusing it. This prevents a restart/rebuild or cache bug from silently falling back to
`settings.spring_mcp_user_id`.

## 5. Actor wiring — replacing hardcodes

- `post_message`: `ThreadMessage.actor_id = actor.user_id` (was the literal `"operator"`).
- `SessionRegistry.create(...)` / `get_or_create_runtime(...)` and `build_session_runtime(...)`: pass the
  authenticated `Actor` (or an immutable runtime actor DTO) so `build_mcp_client(..., user_id=...)` uses
  `str(actor.spring_user_id)` for all Spring MCP tools, including `request_approval`. This replaces the
  current runtime hardcode `settings.spring_mcp_user_id`.
- The same conversation `session_id` is passed on both Spring paths: MCP tool calls
  (`build_mcp_client(..., session_id=session_id)`) and approval REST calls
  (`ApprovalClient(..., session_id=session_id)`). Spring's `isSameActor` checks both `userId` and
  `sessionId`, so a mismatch here must fail tests.
- `approve_approval` / `reject_approval`: build the `ApprovalClient` with
  `X-User-Id = actor.spring_user_id` (was `settings.spring_mcp_user_id`), and stamp status/execution
  messages with `actor_id = actor.user_id`. `ApprovalClient.from_settings(..., user_id=...)` already
  supports the override; `_approval_client`/`make_approval_client` thread the per-actor id through.

### 5.1 Role-shaped runtime

RBAC gates proposal creation, not only approval execution. Otherwise a viewer could chat their way into
`order-manager`, create a real pending approval owned by their `spring_user_id`, and then be forbidden
from acting on it. This slice chooses the stricter behavior:
- Operator runtimes include the current full specialist map: `sales-analyst` + `order-manager` with the
  `request_approval` tool.
- Viewer runtimes include read/analysis capability only. They do **not** build/expose the order-manager
  specialist, and no runtime available to a viewer contains `request_approval`.
- If routing selects an unavailable specialist for the actor's role, `RoutedSessionAgent` returns a short
  policy-denied assistant answer and emits a route/policy diagnostic, without delegating to another
  specialist and without calling any tool. Do not silently fall back to `sales-analyst` for write intent;
  that would hide an authorization decision as a routing choice.
- Tests assert a viewer write-intent turn cannot produce an `agent_proposal` message and no
  `request_approval` tool call is observed.

Spring then validates the approval is owned by that actor (`isSameActor`) — so a user can only
approve/execute their own session's approvals, enforced at the backend, not just the gateway.

## 6. Audit query API — `audit/` + `api/audit.py`

Read-only, **operator-only** (`require(AUDIT_SEARCH)`).

- `audit/query.py`: `AuditQuery` (filters — `actor_id`, `approval_id`, `session_id`, `type`, `since`,
  `until`, `limit`, pagination cursor) and an `AuditStore` protocol with `search(query) -> list[...]`.
- Mongo impl queries the existing thread-messages collection **across sessions**; an in-memory double
  mirrors it. New indexes on `actor_id`, `approval_id`, and `created_at`.
- `GET /api/audit/messages?actor_id=&approval_id=&session=&type=&since=&until=&limit=` returns matching
  messages newest-first with the correlation spine (`turn_id/trace_id/actor_id/execution_id/
  approval_id`) intact.

This is the "who did what, with which data, under which approval?" surface. The spine already exists on
`ThreadMessage`; the new work is the cross-session query (today listing is per-session only) and the
operator gate. No console UI this slice — a browse/search panel is a fast-follow.

## 7. Retention policy

Config-driven window `audit_retention_days` (default **90**). Mechanism = a **Mongo TTL index** on a
BSON `Date` field `expire_at = created_at + window`, set at insert on **thread messages** and **trace
records**. Retention therefore bounds how far audit search can look back — a single, documented policy.
(The current `created_at` is an ISO *string*; TTL requires a real `Date`, so we add `expire_at` rather
than retrofit the string.) The in-memory/test path uses an equivalent age-based sweep so the policy is
testable without Mongo. Session metadata records are tiny and retained; their messages expire.

## 8. Cross-repo (Java) — small, binding already exists

Per-actor binding and cross-actor denial already ship in Spring (`TrustedActorFilter`,
`ApprovalController.isSameActor`, `ApprovalService.validateActor`). This slice:

1. **(Python)** FastAPI sends the authenticated `actor.spring_user_id` as `X-User-Id` (§5) and the same
   conversation `session_id` as `X-Session-Id` for both Spring MCP agent tools and approval REST calls —
   replacing the fixed `"1"`. This is the change that makes per-actor binding *real* end-to-end.
2. **(Java test)** Add/confirm a controller test that actor B is denied actor A's approval on `GET`,
   `approve`, and `execute` (404/denied) — locking the isolation guarantee at the backend.
3. **(Java doc)** Update `docs/2026-06-05-ecommerce-mcp-server-spec.md` to document the trust contract:
   the FastAPI gateway authenticates humans; Spring trusts the service token and binds to the asserted
   `X-User-Id`; approvals are owned by `(userId, sessionId)` and access is actor-scoped.

**No new Java endpoints.** The execute-by-`approval_id` companion and ownership enforcement already
landed; this slice does not reopen the executor logic, only its actor source and documentation/tests.

## 9. CLI

A seed command (e.g. `users add --username <u> --role <viewer|operator> --spring-user-id <n>`,
prompting for a password) creates a user with a hashed password in the Mongo `users` collection. Used
to bootstrap the first operator (migration, §11) and add users without redeploy. Mirrors the existing
`eval`/CLI subcommand structure in `cli.py`.

## 10. Error handling

- **401** — missing/unknown/expired login cookie (`current_actor`); bad login credentials (generic,
  no user enumeration).
- **403/404** — role lacks the action (`require`), or non-owner session access (§4). 404 preferred for
  session access to avoid existence disclosure.
- Existing approval error semantics (`ApprovalApiError`, retry, 409/503) unchanged.
- **CSRF** — cookies + state-changing POSTs: `SameSite=Lax` + same-origin SPA is the mitigation. An
  explicit CSRF token (double-submit/header) is reserved as defense-in-depth, not built this slice.

## 11. Risks & open decisions

- **R-A: `spring_user_id` semantics.** Spring uses `userId` for approval ownership *and* some
  data-scoping queries (e.g. `CustomerOrderService.queryOrders(userId, ...)`). Mapping each operator to
  a `Long` is explicit per-user, but conflates "the operator" with "a business user." Flagged to watch;
  for now each user record carries an explicit `spring_user_id`. Revisit if operator-as-staff needs a
  privileged/unscoped id distinct from a business customer id.
- **R-B: migration.** Existing sessions have no `owner_id` and there are no users yet. Seed ≥1 operator
  (§9); backfill legacy ownerless sessions to a designated seed owner, or treat them as inaccessible.
  Decide at build time; default = backfill to the seed operator.
- **R-C: slice size.** Largest slice so far (auth + isolation + actor wiring + audit + retention +
  cross-repo). Mitigation: the build order (§13) keeps each step independently verifiable; it remains
  one spec / one review as intended.
- **R-D: CSRF.** Mitigated by `SameSite=Lax` + same-origin; token reserved (§10).
- **Decided:** HttpOnly server-side session cookie (fits the existing SSE/`EventSource` streaming —
  cookies are auto-sent where bearer headers cannot be; revocable; XSS-safer than a JS-held token);
  Mongo-seeded user store; `viewer`/`operator` roles via a central `can()`; isolation applies to all
  roles with cross-session access only via the operator audit API; retention default 90 days.

## 12. File structure

**New (Python)**
- `src/ecommerce_agent/auth/__init__.py`, `models.py`, `passwords.py`, `users_store.py`,
  `login_sessions.py`, `permissions.py`, `dependencies.py`
- `src/ecommerce_agent/api/auth.py`
- `src/ecommerce_agent/audit/__init__.py`, `query.py`, `store.py`
- `src/ecommerce_agent/api/audit.py`
- `tests/test_passwords.py`, `tests/test_permissions.py`, `tests/test_auth_stores.py`,
  `tests/test_auth_api.py`, `tests/test_session_isolation.py`, `tests/test_audit.py`,
  `tests/test_retention.py`

**Modified (Python)**
- `src/ecommerce_agent/sessions/store.py` (`owner_id` on create/list/get)
- `src/ecommerce_agent/sessions/registry.py` (thread runtime actor through `create` / runtime rebuild;
  cached-runtime owner check)
- `src/ecommerce_agent/sessions/factory.py` (build Spring MCP client with the actor's `spring_user_id`;
  role-shaped specialist/tool surface)
- `src/ecommerce_agent/api/sessions.py` (`current_actor` deps, ownership 403/404, actor wiring)
- `src/ecommerce_agent/api/app.py` (wire user/login-session/audit stores, mount auth + audit routers,
  cookie config)
- `src/ecommerce_agent/config.py` (auth secret/cookie settings incl. `auth_cookie_secure`,
  `auth_session_ttl_seconds`, `audit_retention_days`, password scheme)
- `src/ecommerce_agent/approvals.py` (per-actor `X-User-Id` plumbing)
- `src/ecommerce_agent/threads/mongo.py` (`expire_at` Date + TTL index; audit indexes)
- `src/ecommerce_agent/trace/mongo.py` (`expire_at` Date + TTL index)
- `src/ecommerce_agent/cli.py` (`users add` seed command)
- relevant `tests/test_cli.py`, `tests/test_*sessions*` updates

**Modified (Frontend)**
- `frontend/src/api/client.ts` (auth API helpers, 401 handling)
- `frontend/src/App.tsx` and/or auth components (minimal login/logout/me shell)
- relevant frontend tests for login, logout, 401-to-login, and existing session calls with cookie auth

**Cross-repo (Java)**
- `ecommerce-mcp-server`: add/confirm a cross-actor denial controller test;
  update `docs/2026-06-05-ecommerce-mcp-server-spec.md` (trust-boundary contract).

## 13. Build order (for the plan)

1. `auth/passwords.py` + `auth/permissions.py` (pure, fully unit-tested first).
2. `auth/models.py`; `users_store.py` + `login_sessions.py` (in-memory doubles + Mongo impls + tests).
3. `auth/dependencies.py` (`current_actor`, `require`) + `api/auth.py` (login/logout/me) + config
   (secret/cookie/ttl/`auth_cookie_secure`) + wire into `app.py` (auth API tests:
   401/403/login/logout/me and cookie flags for local/prod config).
4. Minimal frontend auth shell: `/me` bootstrap, login/logout, 401-to-login handling, and tests.
5. Session ownership: `sessions/store.py` + `registry.py` (`owner_id`); isolation enforcement across
   `api/sessions.py` (isolation tests: A cannot see B across every endpoint).
6. Runtime actor wiring: `SessionRuntime` carries owner/spring ids; registry create/rebuild takes the
   actor and rejects cached owner mismatch; `build_session_runtime` passes `actor.spring_user_id` into
   `build_mcp_client` (wiring tests assert no `settings.spring_mcp_user_id` fallback), and both MCP and
   approval REST paths use the same conversation `session_id`.
7. Role-shaped proposal capability: runtime construction routes through `can(role, PROPOSE)`; viewer
   runtimes cannot expose `order-manager` / `request_approval`; write-intent viewer turns return a
   policy-denied assistant answer with no proposal/tool call.
8. Actor wiring: `ThreadMessage.actor_id` + per-actor `X-User-Id` in approve/reject (wiring tests).
9. Audit: `audit/query.py` + `audit/store.py` (+ indexes) + `api/audit.py` (filter tests; viewer-403).
10. Retention: `expire_at` + TTL indexes on threads + traces; in-memory sweep (retention tests).
11. CLI `users add` seed command (+ dispatch test); migration/backfill (R-B).
12. Cross-repo: Python now sends real `spring_user_id`; Java cross-actor denial test; Java spec update.
13. Full-suite + scoped ruff/frontend verification (Python/TS) and the Java suite (cross-repo).

## 14. Acceptance criteria

1. An unauthenticated request to any conversation or audit endpoint returns 401; `login` sets an
   HttpOnly cookie and `me` returns the actor; `logout` revokes it. Cookie `Secure` behavior is
   configurable and tested for local/prod settings.
2. Passwords are stored hashed (argon2); bad credentials fail generically with no user enumeration.
3. The SPA can bootstrap `/me`, login, logout, recover to login on 401, and use the existing session UI
   with the HttpOnly cookie.
4. A user sees and acts on only their **own** sessions across every conversation endpoint (403/404
   otherwise); cross-session reads are possible **only** via the operator audit API.
5. RBAC routes through a single `can(role, action)`: a `viewer` cannot create approval proposals,
   approve/reject/execute, or run audit search; an `operator` can.
6. Viewer runtimes do not expose `order-manager` / `request_approval`; write-intent viewer turns produce a
   policy-denied answer and no pending approval record.
7. Every appended message, Spring MCP tool call, and approval REST call carries the real authenticated
   `actor_id` / `spring_user_id`; Spring binds and enforces actor ownership (cross-actor approval access
   denied). MCP and approval REST use the same conversation `session_id`.
8. `GET /api/audit/messages` filters across sessions by `actor_id`/approval/session/type/time,
   operator-only.
9. Thread messages and trace records expire per `audit_retention_days` via a Mongo TTL index (sweep on
   the in-memory path).
10. A CLI seed command creates users; the Java spec documents the trust-boundary contract.
11. Default Python suite + scoped ruff pass; frontend tests pass; the Java suite passes including the new
    cross-actor test.
