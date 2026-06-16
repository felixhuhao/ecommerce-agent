# M4 Slice 11 — Sandbox + Local Stack Organization Design

## 1. Goal

Make the local runtime shape match the product architecture:

- Mongo, chart MCP, chart renderer, and local sandbox infrastructure are owned by the
  `ecommerce-agent` stack.
- Java `ecommerce-mcp-server` and MySQL remain a separate backend stack with a separate lifecycle.
- Sandbox execution moves toward a long-lived, service-like local substitute for a future remote
  executor, with session-scoped workspace isolation instead of ad-hoc orphan containers.

This slice is infrastructure hygiene and a sandbox lifecycle seam. It should not change agent
behavior, approval safety, grounding, routing, or the business tool catalog.

## 2. Current State

Observed local Docker state:

- `ecommerce-agent-chart-mcp-1` and `ecommerce-agent-chart-renderer-1` are Compose-managed by this
  repo when using `compose.chart-mcp.yml`.
- `ecommerce-agent-mongo` is Compose-managed by `docker-compose.yml` (since `09e2f15`) with
  `container_name: ecommerce-agent-mongo` and a named volume `mongo-data`. The compose file has no
  explicit project `name:` key or service labels, and a developer machine that started Mongo
  manually before this commit may still have an orphaned `ecommerce-agent-mongo` container using
  anonymous volumes.
- `ecommerce-mcp-server` is standalone and talks to `dev-mysql`, which belongs to a separate MySQL
  stack.
- `ecommerce-sandbox-*` containers are runtime-created `DockerSandbox` containers. Old debug
  containers can remain running for days if not cleaned up.
- This repo has `docker-compose.yml` and `compose.chart-mcp.yml`; it does not have
  `docker-compose.dev.yml`.

Code state:

- `build_session_sandbox(settings, *, session_id)` creates a `DockerSandbox` per session
  (`session_id` is required keyword-only); return type is currently `DockerSandbox`, not
  `BaseSandbox`.
- `DockerSandbox` lazy-creates a Docker container named `ecommerce-sandbox-{session_id}` and removes
  it on `close()`.
- The session registry closes the sandbox when a runtime is evicted or reaped.
- DeepAgents sees the sandbox through `BaseSandbox` methods (`execute`, `upload_files`,
  `download_files`, etc.).

## 3. Research Summary

Mature code execution products commonly use session/task-scoped sandboxes rather than a new
environment for every single code cell or command:

- OpenAI/Azure Code Interpreter uses session-scoped code interpreter lifetimes
  ([OpenAI](https://developers.openai.com/api/docs/assistants/tools/code-interpreter),
  [Azure](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/code-interpreter)).
- E2B supports long-lived sandboxes, pause/resume, and filesystem persistence
  ([lifecycle](https://e2b.dev/docs/sandbox), [persistence](https://e2b.dev/docs/sandbox/persistence)).
- Modal Sandboxes have object lifetimes, idle timeouts, snapshots, and gVisor isolation
  ([sandboxes](https://modal.com/docs/guide/sandboxes),
  [networking/security](https://modal.com/docs/guide/sandbox-networking)).
- Daytona emphasizes persistent sandbox workspaces for long-running agents
  ([overview](https://www.daytona.io/),
  [process/code execution](https://www.daytona.io/docs/en/process-code-execution/)).

The pattern to copy is not "one global shared workspace." It is:

```text
bounded sandbox lifetime
+ session/task/workspace isolation
+ explicit cleanup/TTL
+ resource limits
```

## 4. Decisions

| Topic | Decision | Rationale |
| --- | --- | --- |
| Mongo ownership | Move Mongo fully under this repo's Compose project with a named volume | It is agent-owned state and should start/stop with the agent stack. |
| Java MCP + MySQL | Keep separate from this repo's Compose project | Separate repo/lifecycle; backend data belongs to the Java service. |
| One-command local startup | Provide a thin documented command/script that starts both stacks, not one giant Compose file | Keeps ownership clean while reducing operator friction. |
| Sandbox target | Long-lived local sandbox service/substitute for remote executor, not a fresh container per command | Matches code-interpreter products and avoids cold-start churn. |
| Workspace isolation | Session-scoped workspace first; optional turn subdirectories where the backend has turn context | Matches current `BaseSandbox` shape without forcing a broad turn-context refactor. |
| Security posture | Trusted local/dev analytical code, with resource limits and no direct business-data access from sandbox | Current scripts are model-generated for internal analytics, not arbitrary uploaded user code. |
| Current DockerSandbox | Keep as compatibility path until the service client is proven | Limits blast radius and preserves existing integration tests. |

## 5. Target Local Stack

Recommended repo-owned stack:

```text
ecommerce-agent            # target end-state; sandbox-executor lands in Phase B
├── mongo                  # already Compose-managed (docker-compose.yml)
├── chart-renderer         # already Compose-managed (compose.chart-mcp.yml)
├── chart-mcp              # already Compose-managed (compose.chart-mcp.yml)
└── sandbox-executor       # new in Phase B; local substitute for future remote code executor
```

Separate backend stack:

```text
ecommerce-mcp-server
├── Java/Spring MCP service
└── mysql
```

The Python/FastAPI app can still run locally via `uv run ...` during development. Containerizing the
Python app is not required for this slice.

## 6. Sandbox Service Model

### 6.1 Workspace Model

Minimum viable isolation:

```text
/workspaces/{session_id}/
```

Optional turn-level refinement:

```text
/workspaces/{session_id}/turns/{turn_id}/
```

Session-level persistence is useful because DeepAgents may write files and then execute code over
multiple tool calls in the same agent turn. It also matches the current `DockerSandbox` behavior
where `/workspace` persists for the session.

Turn-level directories are desirable for cleanup and artifact clarity, but DeepAgents' `BaseSandbox`
methods do not currently receive `turn_id`. Enforcing turn directories requires one of:

- a turn-aware backend wrapper that sets current turn context before agent execution, or
- passing a per-turn sandbox instance into each `run_turn`, or
- keeping session-level workspace isolation in this slice and adding turn scoping later.

Default for this slice: session-level workspace isolation.

### 6.2 Service API Shape

The local sandbox executor should expose a small API that can be wrapped as a DeepAgents
`BaseSandbox`:

```text
POST   /sessions/{session_id}/execute
POST   /sessions/{session_id}/files
GET    /sessions/{session_id}/files/{path:path}
DELETE /sessions/{session_id}/files/{path:path}
DELETE /sessions/{session_id}
POST   /maintenance/reap
GET    /health
```

`{path:path}` is a catch-all (Starlette/FastAPI) so nested paths and embedded slashes are captured
as one segment and URL-decoded by the router; a plain `{path}` would not.

**Wire format: sandbox-absolute paths, not workspace-relative.** The path on the wire is the same
**sandbox-absolute** form DeepAgents already hands `DockerSandbox` — either `/workspace/foo.csv` or
an edit-temp file `/tmp/.deepagents_edit_<id>`. A workspace-relative tail cannot express the
edit-temp root, so the API uses sandbox-absolute paths consistently across verbs:

- all three file verbs take the path the same way: the catch-all carries the sandbox-absolute path
  with its **leading `/` stripped** (e.g. `workspace/foo.csv`, `tmp/.deepagents_edit_abc`) and the
  server restores the leading `/`. Because leading-slash stripping puts the path into a URL
  segment, the client **percent-encodes** the stripped segment before sending (reserved chars,
  spaces, `%`, embedded `/`-adjacent bytes) so URL normalization cannot collapse or rewrite it;
  the router URL-decodes it back before `_sandbox_file_path` runs.
- `POST /sessions/{id}/files` (upload) — JSON body
  `{"files": [{"path": "<sandbox-absolute>", "content_b64": "<base64>"}]}`. Bytes are base64-encoded
  so arbitrary/binary (non-UTF-8) payloads survive intact — this matches `DockerSandbox`, which
  already base64-encodes content internally, and is symmetric with download. Per-file size is capped
  at the upload limit (`_MAX_UPLOAD_BYTES`) **before** base64 expansion. The response is a JSON list
  mirroring `FileUploadResponse` — one `{"path", "error"}` per input file — supporting **per-file
  partial success** (some files upload while others are rejected); `error` is `null` on success,
  else one of `invalid_path` / `permission_denied` / `file_not_found`.
- `GET /sessions/{id}/files/{path:path}` (download) — JSON response `{"path", "content_b64",
  "error"}` mirroring `FileDownloadResponse`: `content_b64` holds the base64-encoded bytes or
  `null`; `error` is `null` / `file_not_found` / `is_directory` / `permission_denied` /
  `invalid_path`.
- `DELETE /sessions/{id}/files/{path:path}` — deletes the single confined file; `404` if absent.

Both client and server apply the normalization rule already proven in `_sandbox_file_path`
(`src/ecommerce_agent/sandbox/backend.py`):

- the leading `/` is restored; `posixpath.normpath` is applied;
- a path is accepted only if it normalizes **under `/workspace/`** *or* the DeepAgents edit-temp
  prefix `/tmp/.deepagents_edit_*` (required by inherited `write()`/`edit()`, see §8 parity);
- anything outside those two roots, any `..` traversal, the bare roots themselves, or an empty
  basename is rejected; the server re-validates identically (defense-in-depth) and returns `400`.

How the server backs `/workspace` to on-disk storage (e.g. a per-session
`/workspaces/{session_id}/` directory, §6.1) is an internal choice; on the wire DeepAgents always
sees `/workspace/...`, identical to `DockerSandbox`. This is the same confinement `DockerSandbox`
enforces, so a remote executor cannot introduce a looser traversal rule than the current backend,
and large-edit temp-file parity (§8) is expressible without special-casing.

The Python app owns a `RemoteSandboxClient(BaseSandbox)` that translates DeepAgents calls into this
API. Later, the URL can point to a real remote executor without changing agent construction.

**`close()` is a synchronous/blocking contract.** `BaseSandbox` (external,
`deepagents.backends.sandbox`) only requires `id`, `execute`, `upload_files`, `download_files`. The
session registry additionally calls `sandbox.close()` on eviction/reap, but the close path is
synchronous end to end: `SessionRuntime.close()` (`src/ecommerce_agent/sessions/registry.py`) is a
sync method that calls `sandbox.close()`, and `_close_evicted` runs it inside
`asyncio.to_thread(...)`. `DockerSandbox.close()` is already sync/blocking (it force-removes the
container). Therefore `RemoteSandboxClient` MUST also be a fully **synchronous** `BaseSandbox` —
`execute`, `upload_files`, `download_files`, and `close()` all blocking (use a sync HTTP client such
as `httpx.Client`, or bridge an async client to blocking). `close()` issues
`DELETE /sessions/{session_id}` synchronously and returns only after the server confirms deletion.

Why this is a hard rule: if `RemoteSandboxClient.close()` were `async` (e.g. built on
`httpx.AsyncClient`), the sync `SessionRuntime.close()` would invoke it, receive an un-awaited
coroutine, issue *no* `DELETE`, and silently leak the workspace (plus a "coroutine was never
awaited" warning). The thread offload already keeps a blocking `close()` off the event loop, so
there is no benefit to an async `close()`. The rejected alternative — making the registry/runtime
close path `awaitable` — would require rewriting `_close_evicted`, `create`, `reap_idle`,
`close_all`, and `get_or_create_runtime`, and is unnecessary churn since the sync contract works
today. A unit test asserts `asyncio.iscoroutinefunction(RemoteSandboxClient.close)` is `False` and
that `close()` issues the `DELETE`.

### 6.3 Cleanup

Cleanup rules:

- Workspace TTL is tied to the **session** idle TTL (`session_idle_ttl_seconds`, default 1800) by
  default — that is the TTL the registry reaper actually enforces. The separate
  `sandbox_idle_ttl_seconds` (default 600) is parsed into `SandboxLimits` but not used for
  self-reaping today.
- Deleting a session removes its workspace.
- Startup may reap expired workspaces.
- A manual cleanup command removes stale workspaces and legacy `ecommerce-sandbox-*` containers.

The service must not rely on the model to avoid stale paths. Cleanup and workspace routing are
infrastructure responsibilities.

### 6.4 Resource Controls

Keep the existing sandbox limits concept:

- memory limit
- CPU limit
- PID limit
- per-execute timeout
- output truncation
- upload size cap
- path confinement inside workspace
- no business-network access from executed code

For the local service container, these limits split across layers:

- Compose/container limits for the executor service (memory, CPU, PIDs).
- Per-execute timeout, output truncation, upload cap, and path validation inside the service.

**Network isolation is the one control that does not transfer trivially.** `DockerSandbox` gets
air-tight isolation from `network_mode="none"` on each container
(`src/ecommerce_agent/sandbox/config.py`). A shared long-lived executor container exposed over HTTP
sits on a network with Mongo/chart services, and any subprocess it spawns inherits that namespace —
breaking the current guarantee. Phase B must pick one of:

- per-session network namespace created inside the service before execute, or
- the service spawns per-session containers internally (which erodes most of the rationale for a
  long-lived service), or
- an explicit decision to relax network isolation in service mode, documented in §10 with the risk
  accepted.

Phase B cannot ship to default without resolving this; it is a deliberate decision, not an
implementation detail.

### 6.5 Service Exposure Posture

The sandbox executor can execute arbitrary analytical code and delete workspaces, so its exposure is
an explicit Phase B decision, not an accident of the Compose defaults:

- **Decision: localhost-bound + shared token.** Publish the executor port bound to `127.0.0.1` only
  (`127.0.0.1:${SANDBOX_EXECUTOR_PORT:-8081}:8081`), never `0.0.0.0`. Require a shared bearer token
  (header, e.g. `X-Sandbox-Token`) on **every non-health route** — execute, upload, file GET *and*
  DELETE (downloads expose staged business data and generated artifacts), session DELETE, and
  `/maintenance/reap` — validated in constant time. Only `GET /health` is exempt. The Python app
  sends it from `sandbox_executor_token`; the server reads it from its env at startup.
- **Why not Compose-internal-only:** this slice keeps the Python app running on the host via
  `uv run` (§5), and the host app reaches stack services over localhost — the same pattern as
  `mongo_url` (`mongodb://localhost:27017`) and chart-mcp (`localhost:1122`). An unpublished executor
  would be unreachable from the host app. (Containerizing the app and going Compose-internal is a
  larger change explicitly deferred.)
- **Future:** if the Python app moves into Compose (Phase C+), drop the host port entirely
  (Compose-internal only, reachable over the shared network) and keep the token only as
  defense-in-depth, or remove it.
- Bind address and token are config-driven (`sandbox_executor_url`, `sandbox_executor_token`);
  default URL `http://127.0.0.1:8081`. Startup logs the bind address but never the token.

## 7. Compose Organization

Update this repo so the normal local command is:

```bash
docker compose -f docker-compose.yml -f compose.chart-mcp.yml up -d
```

Then either:

- add sandbox executor to `docker-compose.yml`, or
- add `compose.sandbox.yml` and document:

```bash
docker compose -f docker-compose.yml -f compose.chart-mcp.yml -f compose.sandbox.yml up -d
```

Prefer a separate `compose.sandbox.yml` if the sandbox service needs extra build context or resource
settings that would distract from Mongo.

Mongo is already Compose-managed for fresh setups (named volume `mongo-data`). The only remaining
Mongo hygiene is operator cleanup of a pre-existing orphan `ecommerce-agent-mongo` container from
before `09e2f15`, if one exists on a given machine:

- If local data does not matter, `docker rm -f` the orphan and `docker compose up mongo` to recreate
  against the named volume.
- If local data matters, `mongodump` from the orphan then `mongorestore` into the Compose-managed
  container/volume.

Do not silently remove anonymous Mongo volumes. Adding an explicit project `name:` and service
labels to `docker-compose.yml` is optional tidying, not required.

## 8. Implementation Phases

### Phase A — Stack Hygiene

- Make Compose ownership clear for Mongo + chart services.
- Add documented startup command.
- Add a cleanup command/script for stale `ecommerce-sandbox-*` containers.
- Add labels to runtime-created `DockerSandbox` containers so cleanup can target them safely.
- Keep existing `DockerSandbox` execution behavior.

This phase is low risk and can land first.

### Phase B — Sandbox Executor Service Seam

- Add a local sandbox executor service container, exposed per §6.5 (localhost + shared token).
- Add `RemoteSandboxClient(BaseSandbox)` in Python — a fully **synchronous** client — implementing
  blocking `close()` → `DELETE /sessions/{id}` (see the `close()` contract in §6.2).
- Widen `build_session_sandbox` return type from `DockerSandbox` to `BaseSandbox`.
- Add config:
  - `sandbox_backend = docker | remote`
  - `sandbox_executor_url`
  - `sandbox_executor_token` (shared bearer; required in `remote` mode)
- Keep `docker` as default until the service passes parity tests.
- Resolve the §6.4 network-isolation question before enabling service mode.
- Parity-test `RemoteSandboxClient` against current `DockerSandbox` behavior:
  - execute command
  - upload/download files
  - path confinement
  - output truncation
  - timeout
  - session workspace persistence
  - session deletion cleanup
  - concurrent first-use / concurrent execute on the same session is safe or explicitly serialized
    (parity with `tests/integration/test_docker_sandbox.py` concurrent test)
  - DeepAgents inherited `write()`/`edit()` behavior, including large-edit via the
    `/tmp/.deepagents_edit_*` temp prefix and its cleanup afterward (parity with
    `tests/integration/test_docker_sandbox.py::test_large_edit_via_deepagents_temp_upload`). This
    is the actual analysis-runtime contract — without it a remote executor could pass
    echo/upload/download but break forecast/chart authoring.
  - helper-kit `ecommerce_analysis` is importable in the executor image (parity with
    `tests/integration/test_docker_sandbox.py::test_helper_kit_is_importable_in_sandbox`), so the
    forecast/chart code paths can run.

### Phase C — Default to Service Mode

- Switch local dev default to the sandbox executor service once parity is proven.
- Keep `DockerSandbox` as fallback or test-only backend.
- Update docs and health checks to report the active sandbox backend.

## 9. Tests

Unit/default tests:

- settings parse `sandbox_backend`, `sandbox_executor_url`, and `sandbox_executor_token`.
- sandbox factory chooses `DockerSandbox` or `RemoteSandboxClient`.
- cleanup helper targets only labeled/name-prefixed sandbox containers.
- path validation rejects traversal outside workspace (including absolute escapes and the
  `/tmp/.deepagents_edit_*` carve-out), mirroring `_sandbox_file_path`.
- `asyncio.iscoroutinefunction(RemoteSandboxClient.close)` is `False` (guards the sync contract).

Integration tests, Docker-gated:

- Compose-managed Mongo starts with the expected named volume.
- sandbox executor `/health` is healthy.
- Token/auth contract (§6.5):
  - `GET /health` succeeds **without** a token.
  - execute, upload, file GET, file DELETE, session DELETE, and `/maintenance/reap` each reject a
    **missing** token and a **wrong** token (401/403).
  - `RemoteSandboxClient` sends `X-Sandbox-Token` (from `sandbox_executor_token`) on every request.
- `RemoteSandboxClient.execute("echo ok")` returns output.
- file upload/download round-trip works.
- files persist within a session.
- deleting a session removes the workspace.
- timeout is enforced.
- concurrent execute on the same session is safe or explicitly serialized.
- DeepAgents `write()`/`edit()` work via the remote client, incl. large-edit temp files and cleanup.
- `ecommerce_analysis` (helper-kit) is importable in the executor image.
- `RemoteSandboxClient.close()` is synchronous, issues `DELETE`, and removes the workspace.
- legacy `DockerSandbox` tests remain green until removed.

Manual checks:

- `docker compose ... up -d` starts agent-owned infrastructure.
- Docker Desktop shows one coherent `ecommerce-agent` stack for repo-owned services.
- old `ecommerce-sandbox-*` debug containers can be cleaned safely.
- Java MCP and MySQL remain separate and reachable.

## 10. Risks

- **Turn-level cleanup may need a broader backend-context change.** Start with session-level
  workspaces unless turn context is already available at the sandbox seam.
- **Long-lived service can accumulate stale files.** TTL and session deletion must be implemented,
  not left to prompt discipline.
- **Service mode is a new failure boundary.** Keep `DockerSandbox` fallback until parity is proven.
- **Network isolation may be relaxed in service mode.** `DockerSandbox` guarantees `network_mode=none`
  per container; a shared executor service cannot inherit that for free. If Phase B does not create
  per-session network namespaces, executed code gains implicit reach to Mongo/chart networks. This
  must be an explicit accept-or-mitigate decision, not a side effect.
- **Compose migration of orphan Mongo containers can lose data if rushed.** Treat anonymous-volume
  orphan cleanup as a manual operator choice; fresh setups are already safe.
- **Not a high-risk arbitrary-code sandbox.** If future scope includes uploaded scripts, package
  installs, shell access, or strict multi-tenant production isolation, revisit gVisor/microVM or
  per-session container isolation.

## 11. Open Questions

1. Should Phase A include migrating data off pre-existing orphan Mongo containers?
   - Default: document the dump/restore path; do not migrate automatically. Fresh setups need no
     migration — Mongo is already Compose-managed with a named volume.
2. ~~Should the sandbox executor be implemented as a small HTTP service in this repo?~~ **Resolved
   by Phase B:** yes — a local sandbox executor service with URL/token/config is specified (§6.2,
   §6.5). The remaining open question is **timing**: build it in Phase B now to exercise the
   remote-executor seam, or defer until the real remote executor API shape firms up. Default: build
   it in Phase B only if we are ready to exercise the seam; otherwise keep `docker` default.
3. Should local default remain `DockerSandbox` after Phase A?
   - Default: yes, until `RemoteSandboxClient` parity tests pass.
4. Should the one-command startup script also start the Java MCP/MySQL stack?
   - Default: optional helper script, but keep Compose projects separate.

## 12. Acceptance

- The repo has a clear local infrastructure command using existing Compose files.
- Mongo is Compose-managed with a named volume for new setups (already true since `09e2f15`); this
  slice only documents operator cleanup of pre-existing orphans.
- Stale sandbox containers have a safe cleanup path.
- The design path for a long-lived sandbox executor is explicit and compatible with DeepAgents
  `BaseSandbox`.
- Java MCP/MySQL remain separate but documented as required external dependencies.
- No agent behavior changes are introduced by Phase A.
