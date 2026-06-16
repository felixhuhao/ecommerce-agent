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
| Security posture | Trusted local/dev analytical code; sandbox has no business-data access, and agent operational data (Mongo) is guarded by auth + the executor never receiving Mongo creds | Current scripts are model-generated for internal analytics, not arbitrary uploaded user code. |
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
- `/tmp/.deepagents_edit_*` resolves to `/workspaces/{session_id}/.tmp/...` for **both** the file API
  and `execute` — the same per-session location the bwrap namespace binds as `/tmp` (§6.6). This is
  required because DeepAgents `_edit_via_upload` writes these temps via `upload_files()` then reads
  them via `execute()`; a service-global `/tmp` would break large-edit parity.
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
`DELETE /sessions/{session_id}` synchronously and returns only after the server confirms deletion,
bounded by a timeout so a hanging executor cannot block the registry's `asyncio.gather` close path.

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
(`src/ecommerce_agent/sandbox/config.py`) — no outbound internet and no reach to any other service.
A shared long-lived executor container exposed over HTTP does not inherit that for free. Decision
for Phase B service mode:

- **Decision: dedicated non-`internal` executor network + Mongo authentication (primary control) +
  localhost-bound Mongo/chart ports (defense-in-depth).** Put `sandbox-executor` on its own Compose
  network (a normal bridge, **not** `internal: true`), not attached to the default network where Mongo
  and the chart services live; the executor publishes `127.0.0.1:${SANDBOX_EXECUTOR_PORT:-8006}:8000`
  for the host Python app. **The primary control is Mongo authentication** (§7): the executor may open
  a TCP connection to Mongo but is rejected without credentials, so it cannot read agent operational
  data (sessions, threads, audit, traces, auth). Auth is load-bearing because network topology alone
  is insufficient on Docker Desktop (this target): `host.docker.internal` auto-resolves (to
  `192.168.65.254`) for every container and bypasses `127.0.0.1`-binding for published ports —
  verified, the executor reached a `127.0.0.1`-bound Mongo via `host.docker.internal:27018`. The
  dedicated network still blocks service-name resolution (`gaierror`), and localhost-binding still
  blocks the raw gateway IP (`ConnectionRefusedError` to the discovered gateway, e.g. `172.21.0.1`);
  both are defense-in-depth. Token auth (§6.5) is kept regardless.
- **Hard rule: the executor never receives Mongo credentials.** Mongo auth is the primary control
  *only if* the executor cannot authenticate. `sandbox-executor` must NOT be given `MONGO_URL`,
  `MONGO_INITDB_ROOT_PASSWORD`, or any Mongo credential — via `environment`, `env_file`, a mounted
  `.env`, or staged workspace files. In particular, do **not** point the executor service at the app's
  shared `.env`. Gated by a §9 test that `execute("env")` and the staged workspace expose no Mongo
  credentials.
- **Residual, accepted: outbound internet.** Unlike `DockerSandbox`'s `network_mode="none"`, the
  executor can still reach the internet (verified `REACHED` to `1.1.1.1:53`). It may also be able to
  open a TCP connection to Mongo's port (via `host.docker.internal` on Docker Desktop), but Mongo auth
  rejects it without credentials. External-internet reach is acceptable for trusted model-generated
  analytical code; data still enters via staging tools and approval gates live outside the sandbox.
- **`internal: true` was tried and rejected (empirically).** `internal: true` does block egress, but on
  Docker Desktop (Linux/WSL2, Docker 29.5.3) it **silently drops published ports** — `docker port`
  reports no mapping and the host gets `Connection refused`, so the host app cannot reach an
  internal-only executor.
  `enable_ip_masquerade=false` was also tried: it keeps published ports but does **not** block egress
  (internet still reachable), so it is no better than the chosen posture. There is no static-topology
  way to get both host-reachability and no-egress on this target. Eliminating the internet residual
  needs per-execute netns (rejected as deceptively hard), containerizing the Python app so the executor
  can be `internal: true` and reached over a shared internal network (Phase C+), or a real remote
  executor/gVisor (production path).
- **Rejected alternatives:** (a) per-execute network namespace inside the service — deceptively hard,
  needs extra capabilities/namespace plumbing and is easy to get subtly wrong; (b) the service spawns
  per-session containers internally — better isolation but re-erodes the cold-start/container
  management complexity the long-lived service exists to remove; (c) attach the executor to the
  Mongo/chart network and accept Mongo exposure — unnecessary; the dedicated network + localhost
  binding avoids it.

After Phase C, `.env.example` defaults local development to service mode; `DockerSandbox` remains the
fully `network_mode="none"` fallback. Before production or arbitrary-code use, replace the local
executor with a remote executor, gVisor/microVM, or per-session container isolation.

### 6.5 Service Exposure Posture

The sandbox executor can execute arbitrary analytical code and delete workspaces, so its exposure is
an explicit Phase B decision, not an accident of the Compose defaults:

- **Decision: localhost-bound + shared token.** Publish the executor port bound to `127.0.0.1` only
  (`127.0.0.1:${SANDBOX_EXECUTOR_PORT:-8006}:8000`), never `0.0.0.0`. Require a shared bearer token
  (header, e.g. `X-Sandbox-Token`) on **every non-health route** — execute, upload, file GET *and*
  DELETE (downloads expose staged business data and generated artifacts), session DELETE, and
  `/maintenance/reap` — validated in constant time. Only `GET /health` is exempt. The Python app
  sends it from `sandbox_executor_token`; the server reads it from its env at startup.
- **Why not Compose-internal-only:** this slice keeps the Python app running on the host via
  `uv run` (§5), and the host app reaches stack services over localhost — the same pattern as
  `mongo_url` (`mongodb://localhost:27017`) and chart-mcp (`localhost:1122`). An unpublished executor
  would be unreachable from the host app. (Containerizing the app and going Compose-internal is a
  larger change explicitly deferred.)
- **Future:** if the Python app moves into Compose (Phase C+), have it join the executor's dedicated
  network (still isolated from Mongo/chart), drop the host port entirely, and keep the token only as
  defense-in-depth, or remove it.
- Bind address and token are config-driven (`sandbox_executor_url`, `sandbox_executor_token`);
  default URL `http://127.0.0.1:8006`. Startup logs the bind address but never the token.
- **Network topology** is decided in §6.4: the executor runs on its own Compose network, isolated
  from Mongo/chart, so this §6.5 only governs host exposure and auth, not service-to-service reach.

### 6.6 Executor Implementation Contract

These defaults pin down how the executor service is built, so Phase B implements one architecture
rather than inventing a second mini-architecture:

- **Execution model: subprocess per `execute`, in its own process group.** Each
  `POST /sessions/{id}/execute` runs the command as an isolated subprocess — closest to
  `DockerSandbox`'s `container.exec_run` (simple output capture/truncation, no persistent shell
  state). Session persistence is **filesystem-only** (files in the workspace); there is no long-lived
  shell or kernel state across executes. **Timeout kills the full process tree**, not just the shell
  parent: the subprocess is started in its own session/process group (`start_new_session=True`) and the
  whole group is signaled on timeout, so a background child cannot survive and keep modifying the
  workspace.
- **Filesystem namespace: per-execute mount namespace (bubblewrap).** A long-lived shared container
  has one global `/workspace`, so `cwd` alone is insufficient — model code and DeepAgents helpers use
  **absolute** `/workspace/...` paths, and without isolation session A's subprocess could read
  `/workspaces/<session_B>`. Each `execute` therefore runs inside a bubblewrap (`bwrap`) mount
  namespace that **binds `/workspaces/{session_id}` as `/workspace`** plus a per-session `/tmp`
  (covering the `/tmp/.deepagents_edit_*` prefix), with the `/workspaces/` parent **not exposed**. This
  makes absolute `/workspace/...` paths resolve to the session's files (parity with `DockerSandbox`'s
  per-container tmpfs) **and** prevents cross-session reads (the process cannot see other sessions'
  directories).

  Minimal bwrap shape for Phase B, finalized by the capability probe rather than by prose alone:

  - bind the session workspace read-write: `--bind /workspaces/{session_id} /workspace`;
  - bind a per-session temp dir read-write: `--bind /workspaces/{session_id}/.tmp /tmp` (the same
    location the file API maps `/tmp/.deepagents_edit_*` to, §6.2 — required for edit parity);
  - expose runtime/system files read-only, starting with `--ro-bind /usr /usr` (covers
    `/usr/local` → python, pandas, numpy), `--ro-bind /bin /bin`, `--ro-bind /lib /lib`,
    `--ro-bind /lib64 /lib64`, `--ro-bind /opt /opt` (helper-kit path), and `--ro-bind /etc /etc`.
    The final allowlist is **probe-derived** (VERIFIED, see below);
  - mount proc with `--proc /proc`;
  - provide device nodes with `--dev /dev` (VERIFIED required — Python/numpy need `/dev/urandom`
    and `/dev/null`; a tmpfs-only `/dev` was not tried since `--dev /dev` worked on first probe);
  - set the in-namespace working directory with `--chdir /workspace` so absolute `/workspace/...`
    paths and the `cwd` both resolve to the session workspace;
  - do **not** bind `/workspaces` or any parent that would reveal sibling sessions.

  Requirement: the executor container must permit mount-namespace creation (`CAP_SYS_ADMIN` or
  unprivileged user namespaces) — a privilege `DockerSandbox` got for free via separate containers;
  this must be **empirically verified** in the executor image. If the bwrap probe fails on the target,
  Phase B must not enable service mode: either stop at the client/config seam with `docker` still
  default, or explicitly switch the service design to internal per-session containers (rejected alt
  (b), revived as the only other safe option).

  **VERIFIED (Phase B2):** the capability probe PASSED end-to-end through the running executor
  service (`ecommerce-agent-sandbox-executor:dev`, built on `ecommerce-agent-sandbox:dev`).
  Required container caps: `--cap-add SYS_ADMIN --security-opt seccomp=unconfined` (unprivileged
  user namespaces are blocked by Docker Desktop's seccomp profile; `SYS_ADMIN` alone fails at
  `pivot_root` without `seccomp=unconfined`). With this privilege set, executed code inside the
  bwrap namespace successfully `import ecommerce_analysis, pandas, numpy`, resolves `/workspace`
  to the session workspace, and is isolated across sessions (§9 cross-session check passes). The
  service runs as root in the container so `CAP_SYS_ADMIN` is in the effective set (§10 accepted
  posture for trusted analytical code).
- **Image strategy: extend the sandbox image.** Build the executor on `ecommerce-agent-sandbox:dev`
  (python + pandas + numpy + `ecommerce_analysis`) and add only the HTTP service layer + deps on top.
  One source of truth for the helper-kit keeps the import-parity test meaningful.
- **Code location: top-level service package.** `sandbox_executor/` (sibling to `sandbox_image/`),
  containing `app.py` (the FastAPI service) and `Dockerfile`, plus `compose.sandbox.yml` at the repo
  root — visually separate from the agent runtime (`src/ecommerce_agent/`), same repo.
- **Session semantics: lazy on first use.** A session's workspace is created on first
  `execute`/`upload`; there is no explicit create route (matches `DockerSandbox`'s lazy container
  creation). `DELETE /sessions/{id}` is **idempotent** — deleting a nonexistent workspace is a
  success/no-op (chosen for cleanup friendliness).
- **Environment: explicit minimal allowlist.** The per-execute subprocess receives only an allowlist —
  positively `PATH`, `PYTHONPATH` (so `import ecommerce_analysis` works), `HOME`, `LANG` — and **no**
  app/Mongo variables (`MONGO_URL`, `MONGO_INITDB_ROOT_PASSWORD`, etc.). This is the mechanism for the
  §6.4 hard rule that the executor never receives Mongo credentials; the §9 `execute("env")` test
  checks both sides (allowed vars present, Mongo creds absent).
- **Concurrency: per-session serialization (v1).** Concurrent `execute`s for the same session are
  serialized with a per-session lock. Subprocess-per-execute over a shared workspace can race if the
  agent issues parallel tool calls; serializing is the boring, sane choice for v1 (revisit true
  parallelism later).

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

**Enable Mongo authentication** (primary control for service-mode isolation, §6.4). Mongo currently
runs unauthenticated; start it with `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` and
point `mongo_url` at `mongodb://<user>:<urlencoded-pass>@localhost:27017/?authSource=admin` — the root
user lives in the `admin` database, so `authSource=admin` is explicit to avoid driver default
ambiguity. **This puts root credentials directly in the app — acceptable for local dev and marked as
such.** The least-privilege upgrade (out of scope for this slice) is to let the root env vars
initialize Mongo and create an app-scoped user, then set
`mongo_url=mongodb://ecommerce_agent_app:<pass>@localhost:27017/ecommerce_agent?authSource=ecommerce_agent`;
note this is app-hardening, not sandbox-isolation (the executor lacks creds either way). Every store
(threads, audit, auth, trace, sessions) authenticates via `mongo_url`. Auth is load-bearing: on
Docker Desktop `host.docker.internal` bypasses `127.0.0.1`-binding (verified), so the executor can
open a TCP connection to Mongo's port but is rejected without credentials. This is good hygiene
regardless of the sandbox — Mongo is currently exposed unauthenticated.

`MONGO_INITDB_ROOT_*` only creates the root user on a **fresh** `/data/db`. An existing `mongo-data`
named volume from a current dev setup already has data and no auth, so the init script will **not**
run and Mongo stays unauthenticated. Since auth is load-bearing, migrating the existing volume is
required, not optional: (a) reset — `docker volume rm` the `mongo-data` volume and
`docker compose up mongo` to reinitialize with auth (local data lost); or (b) preserve data —
`mongodump` unauthenticated from the running container, `docker volume rm mongo-data`, recreate the
container with the `MONGO_INITDB_ROOT_*` env, then `mongorestore` using credentials.

Bind Mongo + chart host ports to `127.0.0.1` only — Mongo (`docker-compose.yml`) and the chart
renderer / chart-mcp (`compose.chart-mcp.yml`) currently publish on `0.0.0.0`. Changing them to
`127.0.0.1:${PORT}:${PORT}` is now **defense-in-depth**, not the primary control: it blocks the raw
gateway IP (`ConnectionRefusedError` to the discovered gateway) and stops exposing Mongo to the LAN.
On Docker Desktop it does **not** block `host.docker.internal` (which bypasses the binding); Mongo auth
covers that vector. The host app already reaches these over `localhost` (`mongo_url`, chart
`localhost:1122`) and other containers reach them by service name, so this breaks nothing.

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

Phase B is split into two tasks for blast-radius control. **B1** (Mongo auth + port-binding) is
security hygiene that lands first and is valuable regardless of whether B2 ships; **B2** (executor
service + client) is the seam, gated on the bwrap probe.

#### Phase B1 — Mongo auth + port-binding cleanup

- Enable Mongo authentication per §7: `MONGO_INITDB_ROOT_*` + `mongo_url` with `authSource=admin`, and
  migrate any existing `mongo-data` volume (§7/§11) — auth only initializes on a fresh `/data/db`.
- Bind the Mongo + chart host ports to `127.0.0.1` only (§7) — defense-in-depth (blocks gateway reach,
  stops LAN exposure of unauthenticated Mongo).
- Verify with the §9 B1 checks: Mongo starts with auth, unauthenticated access is rejected, authenticated
  app access works, and host-facing ports are bound to `127.0.0.1` only. `docker` remains the sandbox
  backend; no executor or executor-network topology check exists yet.

#### Phase B2 — Executor service + bwrap probe + client

> **STATUS: implemented (code + integration-verified).** `sandbox_executor/` (FastAPI service +
> Dockerfile), `compose.sandbox.yml`, `RemoteSandboxClient` + factory seam + config are landed; the
> bwrap preflight and all §9 B2 parity/auth/isolation checks pass against the running executor.

- Add a local sandbox executor service container, exposed per §6.5 (localhost + shared token), on its
  own dedicated **non-`internal`** Compose network (isolated from Mongo/chart).
- **Run the bwrap capability probe first** (§6.6/§9): it must bind a probe workspace as `/workspace`,
  hide the `/workspaces` parent, mount the runtime/helper-kit read-only, and run
  `python -c 'import ecommerce_analysis'` inside the executor image with its capability set. **If the
  probe fails, stop**: keep `docker` default and do not ship remote service mode unless the design is
  explicitly changed to internal per-session containers (rejected alt (b), revived as the only other
  safe option).
- Build the service per the §6.6 implementation contract — subprocess-per-execute in a per-execute
  **bubblewrap namespace** (path-mapping + cross-session isolation), process-group timeout kill, extend
  the sandbox image, top-level `sandbox_executor/` package + `compose.sandbox.yml`, lazy sessions with
  idempotent `DELETE`, explicit env allowlist, per-session execute lock. Do not invent a second
  architecture.
- Add `RemoteSandboxClient(BaseSandbox)` — a fully **synchronous** client — implementing blocking
  `close()` → `DELETE /sessions/{id}` (§6.2). Widen `build_session_sandbox` return type from
  `DockerSandbox` to `BaseSandbox`.
- Add config: `sandbox_backend = docker | remote`, `sandbox_executor_url`,
  `sandbox_executor_token` (shared bearer; required in `remote` mode).
- Keep the no-env code fallback as `docker` until the service passes parity; the local env-template
  default switches to `remote` in Phase C.
- Parity-test `RemoteSandboxClient` against current `DockerSandbox` behavior (§9):
  - execute command
  - upload/download files
  - path confinement
  - output truncation
  - timeout (kills the full process tree)
  - session workspace persistence + absolute `/workspace/...` mapping
  - cross-session isolation (A cannot read B)
  - session deletion cleanup (idempotent `DELETE`)
  - concurrent first-use is safe; concurrent execute on the same session is serialized via a
    per-session lock (v1, §6.6)
  - DeepAgents inherited `write()`/`edit()` behavior, including large-edit via the
    `/tmp/.deepagents_edit_*` temp prefix and its cleanup (parity with
    `tests/integration/test_docker_sandbox.py::test_large_edit_via_deepagents_temp_upload`). This
    is the actual analysis-runtime contract — without it a remote executor could pass
    echo/upload/download but break forecast/chart authoring.
  - helper-kit `ecommerce_analysis` importable via `RemoteSandboxClient.execute` (parity with
    `tests/integration/test_docker_sandbox.py::test_helper_kit_is_importable_in_sandbox`), so the
    forecast/chart code paths can run.

### Phase C — Default to Service Mode

- **STATUS: implemented.** A live `RemoteSandboxClient` smoke passed against the running executor
  (execute, upload/download, helper import, cross-session isolation, timeout, close), and the local
  env-template default is now `SANDBOX_BACKEND=remote`.
- Local dev now defaults to the sandbox executor service in `.env.example`.
- `DockerSandbox` remains the no-env/fallback backend.
- `/health` reports the active sandbox backend.

## 9. Tests

Unit/default tests:

- settings parse `sandbox_backend`, `sandbox_executor_url`, and `sandbox_executor_token`; no-env code
  defaults still fall back to `docker`, while `.env.example` is the local dev `remote` default.
- sandbox factory chooses `DockerSandbox` or `RemoteSandboxClient`.
- cleanup helper targets only labeled/name-prefixed sandbox containers.
- path validation rejects traversal outside workspace (including absolute escapes and the
  `/tmp/.deepagents_edit_*` carve-out), mirroring `_sandbox_file_path`.
- `asyncio.iscoroutinefunction(RemoteSandboxClient.close)` is `False` (guards the sync contract).

Integration tests, Docker-gated:

Phase B1 checks:

- Compose-managed Mongo starts with the expected named volume.
- Mongo auth gate (§6.4/§7): unauthenticated Mongo access is rejected; authenticated app access through
  `mongo_url` succeeds.
- Existing named `mongo-data` migration path is exercised or documented in the test fixture setup
  (reset volume, or dump/restore into an auth-enabled fresh volume).
- Mongo and chart host ports are bound to `127.0.0.1` only, not `0.0.0.0`.

Phase B2 checks:

- sandbox executor `/health` is healthy.
- Token/auth contract (§6.5):
  - `GET /health` succeeds **without** a token.
  - execute, upload, file GET, file DELETE, session DELETE, and `/maintenance/reap` each reject a
    **missing** token and a **wrong** token (401/403).
  - `RemoteSandboxClient` sends `X-Sandbox-Token` (from `sandbox_executor_token`) on every request.
- bubblewrap capability preflight (§6.6): the executor image can run `bwrap` with the intended,
  probe-derived mount layout, bind a probe workspace as `/workspace`, hide the `/workspaces` parent,
  mount `/proc`, provide the minimal runtime/dev surface Python needs, and execute
  `python -c 'import ecommerce_analysis; print("ok")'`. The test records the final read-only bind
  allowlist and whether minimal `/dev` was enough or `--dev /dev` was required. If this fails, service
  mode remains disabled. **VERIFIED (Phase B2):** preflight passes through the running service —
  `import ecommerce_analysis, pandas, numpy` succeeds inside the bwrap namespace; final read-only binds
  are `/usr /bin /lib /lib64 /opt /etc`; `--dev /dev` is used; caps are `SYS_ADMIN + seccomp=unconfined`.
- `RemoteSandboxClient.execute("echo ok")` returns output.
- file upload/download round-trip works.
- host-side file APIs reject symlink escapes, including final-path symlinks and a replaced
  `/workspace/.tmp` root, so executed code cannot make download/upload/delete touch files outside the
  session workspace.
- files persist within a session; **absolute** `/workspace/...` paths resolve to that session's
  workspace (proves the namespace mapping, §6.6).
- **cross-session isolation (§6.6):** executed code in session A cannot list or read session B's
  files (e.g. `execute` in A cannot `cat /workspaces/<B>/...` nor escape via absolute paths).
- deleting a session removes the workspace; `DELETE` is idempotent (deleting a nonexistent workspace
  is a success/no-op, §6.6).
- timeout is enforced and **kills the full process tree**: a command that spawns a background child
  leaves no surviving child after timeout (§6.6).
- concurrent execute on the same session is serialized via a per-session lock (v1, §6.6).
- DeepAgents `write()`/`edit()` work via the remote client, incl. large-edit temp files and cleanup.
- `ecommerce_analysis` (helper-kit) is importable **via `RemoteSandboxClient.execute`** end-to-end
  (the raw-bwrap preflight above checks the namespace directly; this checks client→service parity).
- network topology (§6.4): the host app **can** reach the executor's `127.0.0.1` published port;
  executed code in service mode **cannot** reach Mongo by service-name resolution
  (`ecommerce-agent-mongo`) **nor** via the **dynamically discovered** executor-network gateway IP
  (user-defined Compose bridges do not always use `172.17.0.1` — the test must read the gateway from
  the executor's default route, not hard-code it); on Docker Desktop `host.docker.internal` **is**
  reachable and bypasses `127.0.0.1`-binding (documented, not gated) — so the decisive test is the
  next item. (Executed code **can** still reach the internet `1.1.1.1:53` — accepted egress residual.)
- Mongo auth gate (decisive, §6.4/§7): an **unauthenticated** connection to Mongo — via any reachable
  vector including `host.docker.internal` — is **rejected** by auth; the executor cannot read agent
  data without credentials. This is the load-bearing control.
- credential isolation (§6.4 hard rule, two layers):
  - **container layer:** the `sandbox-executor` service container's own environment
    (`docker exec sandbox-executor env` / compose inspect) exposes no Mongo credentials — catches a
    misconfigured `env_file` / `.env` mount that the per-execute allowlist would otherwise hide.
  - **subprocess + workspace layer:** `RemoteSandboxClient.execute("env")` and a scan of the staged
    workspace expose no Mongo credentials (`MONGO_URL`, `MONGO_INITDB_ROOT_PASSWORD`, etc.).
  Both must pass so auth actually protects — creds neither in the container nor handed to executed code.
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
- **Network isolation in service mode.** `DockerSandbox` guarantees `network_mode=none` per container
  (no outbound internet, no service reach). Phase B's executor runs on a dedicated **non-`internal`**
  Compose network isolated from Mongo/chart (§6.4), so executed code cannot reach Mongo by service
  name, and `127.0.0.1`-binding blocks the raw gateway IP (verified refused). **But on Docker Desktop
  (this target) `host.docker.internal` auto-resolves and bypasses `127.0.0.1`-binding** — the executor
  can open a TCP connection to Mongo's published port (verified reached). The primary protection is
  therefore **Mongo authentication** (§7): the executor is rejected without credentials. The accepted
  residual is **outbound internet**: service mode is local-dev isolation, not `network_mode=none` and
  not production-grade arbitrary-code isolation. `internal: true` would close the egress residual but
  was rejected because it silently breaks published ports on Docker Desktop (verified), so the host app
  could not reach an internal-only executor. Service mode is now the local dev default because parity,
  auth, and topology checks passed; `DockerSandbox` remains the high-isolation fallback.
- **Compose migration of orphan Mongo containers can lose data if rushed.** Treat anonymous-volume
  orphan cleanup as a manual operator choice; fresh setups are already safe.
- **Not a high-risk arbitrary-code sandbox.** If future scope includes uploaded scripts, package
  installs, shell access, or strict multi-tenant production isolation, revisit gVisor/microVM or
  per-session container isolation.
- **Executor needs mount-namespace privilege.** Per-execute filesystem isolation (§6.6) requires the
  executor container to create mount namespaces (`CAP_SYS_ADMIN` or unprivileged user namespaces) —
  more privilege than `DockerSandbox`'s `cap_drop: ALL` per-container model got for free. Acceptable
  for trusted analytical code only if the bwrap probe passes. If it fails, keep `docker` default and
  do not enable service mode without switching to internal per-session containers or another proven
  filesystem-isolation mechanism.

## 11. Open Questions

1. Mongo data migration now that auth is load-bearing.
   - Orphan `ecommerce-agent-mongo` containers (pre-`09e2f15`, anonymous volumes): document the
     dump/restore path; do not migrate automatically.
   - **Existing named `mongo-data` volumes require migration to enable auth** (§7): `MONGO_INITDB_ROOT_*`
     only initializes auth on a fresh `/data/db`, so a volume that already has data stays
     unauthenticated. Reset the volume, or `mongodump` → recreate with auth → `mongorestore` with
     credentials. Required, not optional, because auth is load-bearing for service-mode isolation.
   - Fresh setups still need no migration — the named volume initializes with auth on first start.
2. Should the one-command startup script also start the Java MCP/MySQL stack?
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
