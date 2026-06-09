# Week 2: Sub-Agents + Sandbox — Design Spec

> The analysis-and-charting path: a coordinator main agent routes to a read-only sales-analyst
> sub-agent that runs isolated code in a Docker-backed sandbox and emits chart specs.
> Status: Draft | Date: 2026-06-09
> Parent spec: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)
> Product roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md)
> Builds on: [2026-06-08-week1-foundation-design.md](2026-06-08-week1-foundation-design.md)
> Server contract: [ecommerce-mcp-server spec](../../ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md)

## 1. Scope

Week 1 delivered a single agent + FastAPI/SSE + the SpringBoot MCP read tools. Week 2 builds the
**read-only analysis-and-charting path end to end**: a coordinator main agent that routes to a
**sales-analyst** sub-agent, which analyzes business data by running isolated Python in a
Docker-backed sandbox and produces a chart spec via the ModelScope visualization MCP.

**In scope (Week 2):**
- Main agent as **coordinator** (routes to sub-agents; holds its own tool list; never a tool-less router)
- **sales-analyst** sub-agent (read-only): the 10 SpringBoot read tools + `generate_visualization`
- **DockerSandbox** — a custom DeepAgents backend giving isolated code execution + a sandbox filesystem
- **Visualization** via ModelScope MCP `generate_visualization`, behind a swappable seam
- **YAML prompt management** (migrate the inline Week 1 prompt)
- Two-tier tests (default boundary tests incl. real-Docker sandbox tests; opt-in live smoke)

**Deferred to Week 2.5** (named bucket; dependencies noted):
- File upload (`POST /api/upload`) + `read_uploaded_file` + `write_report` — *sandbox-only, can land
  first; no HITL needed*
- **order-manager** sub-agent + write tools (`purchase_order_create`, `purchase_order_receive`,
  `order_update`, `request_approval`) — *needs Week 3 HITL/checkpoint*
- skills/memory middleware, `web_search`, `assign_skill` — *needs Week 3 skills/memory systems*

**Deferred to Week 3:** HITL interrupt/resume, MongoDB checkpoint, `ContextVar` session isolation,
memory layers, and `CompositeBackend` routing for `/memories` + `/skills`.

**Out of scope:** the operator console milestone — Week 2 verifies a chart *spec* is produced;
rendering belongs to the UI/artifact surface later.

## 2. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Sub-agents | DeepAgents native `subagents` (`SubAgent` dicts via a factory) | First-class framework feature; each sub-agent declares its own `tools`/`skills`/`interrupt_on`. |
| Main agent | Coordinator that **holds its own tools** (file tools + routing) | Never a degenerate router, so `web_search` later is `+1` tool, not new machinery. |
| Exec backend | Custom `DockerSandbox(BaseSandbox)` | Self-hosted isolation, no SaaS; conforms to DeepAgents' backend protocol; swappable for a remote executor later. |
| Sandbox lifecycle | **Persistent per-session container**, reused across `execute` calls | Matches mature code-interpreters (§9): statefulness + no per-call cold start. |
| Statefulness | **Filesystem-stateful** (files persist; each `execute` runs fresh code) | Agent round-trips data through sandbox files; simplest model that fits DeepAgents' shell-based `execute`. Full REPL/kernel state is a later upgrade. |
| Files location | **On the sandbox** (backend = sandbox) | All file tools + `execute` share one workspace; code consumes the files the agent writes. Matches parent §2.2. |
| Visualization | ModelScope MCP `generate_visualization` (declarative, 26→1) **behind a seam** | Conversational-BI products use declarative specs rendered in the UI; compute/render split. Self-hosted renderer is a drop-in if ModelScope is unavailable. |
| Prompts | YAML (`prompts/prompts.yml` + loader) | Parent §4.5; keeps sub-agent definitions thin (config, not prose). |
| Structure | Option C: `sandbox/` package; `agents.py`; `prompts/`; viz seam in `mcp_client.py` | Pre-split only the certain-to-grow, multi-concern piece (sandbox); keep the rest flat with clean seams. |
| Deferred features | `web_search`/`assign_skill`/order-manager deferred with **proven seams** | They are native additive slots (`tools`/`skills`/`interrupt_on`); `build_agent` threads all of them now. |

## 3. Architecture

### 3.1 Project structure (option C)

```
src/ecommerce_agent/
├── config.py            # + sandbox settings (image, mem/cpu/pids caps, timeout, idle TTL)
├── models.py            # unchanged
├── mcp_client.py        # + ModelScope connection enable + viz-tool allowlist seam
├── agent.py             # build_agent(model, *, tools, subagents, middleware, skills, backend)
├── agents.py            # NEW: coordinator config + sub-agent factory (returns SubAgent dicts)
├── prompts/
│   ├── prompts.yml      # NEW: main_agent + sales_analyst prompts
│   └── loader.py        # NEW: tiny typed YAML loader (read once at build)
├── sandbox/             # NEW package (the one certain-to-grow, multi-concern piece)
│   ├── __init__.py      # exports DockerSandbox
│   ├── backend.py       # DockerSandbox(BaseSandbox): execute() + upload_files() + lifecycle
│   └── config.py        # container hardening flags / resource limits builder
└── api/
    ├── app.py           # lifespan builds the sandbox backend + wires it into the agent
    └── chat.py          # unchanged request/SSE contract
```

No `session/`, `middleware/` (custom), or `checkpoint/` modules yet — Week 3 additions.

### 3.2 Agent composition

- **`build_agent(model, *, tools, subagents, middleware, skills, backend)`** — every DeepAgents
  extension slot is a parameter from day one (proven seams). Week 2 passes: coordinator tools,
  `[sales_analyst]`, summarization + call-limit middleware, `skills=[]`, the `DockerSandbox` backend.
- **Coordinator (main agent):** routes to sub-agents and holds its own tools (the backend file
  tools). Prompt from `prompts.yml:main_agent`.
- **sales-analyst:** an `agents.py` factory returns a `SubAgent` dict — `name`, `description`,
  `system_prompt` (from YAML), `tools` = the 10 SpringBoot read tools (reuse Week 1's
  `READ_ONLY_SPRING_TOOLS`) **+** `generate_visualization`, with empty `skills`/`interrupt_on`
  slots left explicit. `execute` + file tools come from the shared backend (no per-tool wiring).

### 3.3 Documented insertion points (no rework later)

| Future capability | Slot | Where |
|-------------------|------|-------|
| `web_search` (Week 2.5) | coordinator `tools` | append one `BaseTool` |
| order-manager (Week 2.5) | a `SubAgent` with write `tools` + `interrupt_on` | `agents.py` factory + `subagents` |
| skills / `assign_skill` (Week 3) | `skills=` + skills middleware | `build_agent` params |
| memory (Week 3) | `middleware=` + `CompositeBackend` | `build_agent` params |

## 4. Sandbox (DockerSandbox)

### 4.1 Conformance & lifecycle

`DockerSandbox(BaseSandbox)` implements `execute()` + `upload_files()`; DeepAgents derives
`read`/`write`/`edit`/`ls`/`glob`/`grep` from those. The **lifecycle** behind those methods is the
design's substance, and it sits entirely behind the `BaseSandbox` seam (swappable for a remote
executor later without touching agents/tools).

- **Persistent per-session container.** Lazy-create on first `execute`; reuse via `docker exec`
  for subsequent calls; tear down on session end / idle TTL / app shutdown.
- **Filesystem-stateful.** Files in `/workspace` persist across `execute` calls; each `execute`
  runs fresh code (`docker exec … python`). Full REPL/kernel state (a persistent IPython kernel)
  is a noted later upgrade and does not change the files-on-sandbox property.
- **Week 2:** a single container (one session). **Week 3:** one container per `session_id`
  ("singleton per session"). Warm pools / memory snapshots are a future scaling technique, not an
  MVP need.

### 4.2 Hardening (`sandbox/config.py`)

Applied to the persistent container; a per-`execute` wall-clock timeout still bounds each call:

- `--network none` — the big one: agent code cannot exfiltrate or reach MySQL / the MCP server.
- `--read-only` rootfs + writable `/workspace` (+ `--tmpfs /tmp`).
- `--user` non-root, `--security-opt no-new-privileges`, `--cap-drop ALL`.
- `--memory`, `--cpus`, `--pids-limit`, ulimits.
- Per-`execute` timeout that kills the call; idle TTL reaps abandoned containers.

Settings in `config.py`: `SANDBOX_IMAGE`, `SANDBOX_MEMORY`, `SANDBOX_CPUS`, `SANDBOX_PIDS`,
`SANDBOX_EXECUTE_TIMEOUT_SECONDS`, `SANDBOX_IDLE_TTL_SECONDS`.

### 4.3 Sandbox image (deliverable)

Because `--network none` blocks runtime `pip install`, ship a **prebuilt image** with
`python + pandas + numpy` baked in: `Dockerfile.sandbox` → `ecommerce-agent-sandbox:dev`.

### 4.4 Files-on-sandbox & the data path

The DeepAgents backend *is* the sandbox, so the agent's file tools and `execute` share one
workspace. MCP query **results return to the agent's context** (text); when the agent wants to
analyze them, it `write_file`s them into `/workspace` and sandboxed code consumes them there —
files used by code never touch the host (parent §2.2). Week 2 uses a single `DockerSandbox`
backend; Week 3 wraps it in a `CompositeBackend` that keeps `/workspace` on the sandbox while
routing `/memories` and `/skills` to their own backends (parent §12).

## 5. Visualization (ModelScope MCP, behind a seam)

- Enable the **ModelScope MCP connection** in `mcp_client.py` (the `MODELSCOPE_MCP_URL` config seam
  exists from Week 1); tools are discovered like SpringBoot's.
- A **viz-tool allowlist** (parallel to `READ_ONLY_SPRING_TOOLS`) exposes only
  `generate_visualization` to the sales-analyst.
- The agent's contract is **"emit a chart spec"**; ModelScope renders the declarative config. If
  ModelScope is unreachable at implementation time, the seam swaps in a self-hosted declarative
  renderer without touching the agent. *Implementation-time item: confirm ModelScope endpoint/token.*
- **Compute/render split:** sandbox computes aggregates → `generate_visualization` produces the
  spec → the operator console displays it later. Week 2 verifies the spec is produced.

## 6. Prompts (YAML)

`prompts/prompts.yml` holds `main_agent` (coordinator/routing) and `sales_analyst` prompts, loaded
once by `prompts/loader.py` at agent build. Week 1's inline `SYSTEM_PROMPT` migrates here.
Sub-agent definitions reference prompt keys, keeping `agents.py` thin.

## 7. Data flow (the Week 2 demo)

"Compare sales by category":

```
POST /api/chat/stream {message}
 → coordinator routes → sales-analyst
 → sales-analyst calls get_statistics / order_query (SpringBoot MCP) → data into context
 → write_file(result.json) [sandbox] → execute(pandas: group by category) [sandbox] → aggregates
 → generate_visualization(spec from aggregates) [ModelScope MCP] → chart spec
 → sales-analyst returns analysis + chart spec → coordinator → SSE stream
```

SSE frames are unchanged (`token` / `tool` / `done` / `error`); `tool` frames now also surface
`execute` and `generate_visualization`, so the boundary is observable in tests.

## 8. Testing & acceptance

Carries Week 1's two-tier shape.

**Default boundary tests (deterministic, no LLM):**
- `sandbox/` against **real Docker**: `execute` runs code; `--network none` enforced (a network
  call fails); timeout + resource caps respected; files persist across `execute` calls in one
  session; container torn down. **Skips cleanly when Docker is absent** (like the Spring-reachable
  skip).
- `agents.py` / `build_agent`: sub-agent factory wires the right tool allowlists; coordinator holds
  its own tools; all extension slots threaded.
- viz allowlist + ModelScope connection registry (mirrors the Spring allowlist tests).
- prompt loader.

**Opt-in live smoke (`RUN_LIVE_LLM=1`):** "compare sales by category" → assert the stream shows
`execute` **and** `generate_visualization` tool events and completes. Run before dependency bumps
(DeepAgents/LangGraph/LangChain/MCP adapters) per the Week 1 gate.

**Acceptance (definition of done):**
- Coordinator routes to sales-analyst.
- sales-analyst analyzes seeded data in the sandbox and emits a chart spec via ModelScope (or the
  seam's renderer).
- Default suite green, including the real-Docker sandbox boundary tests (skipped if no Docker).
- Live smoke passes by hand.

## 9. Research basis (sandbox lifecycle)

The persistent-per-session model (not a fresh container per `execute`) follows mature
code-interpreter practice:
- E2B runs a long-lived per-session sandbox with a Jupyter kernel that maintains state across
  executions ([E2B architecture](https://deepwiki.com/e2b-dev/code-interpreter/2.1-sandbox-environment),
  [ZenML](https://www.zenml.io/blog/e2b-vs-daytona)).
- Google's GKE Agent Sandbox treats the agent runtime as "a singleton: one isolated environment
  per user session or task," with persistent storage and pause/resume
  ([Google OSS blog](https://opensource.googleblog.com/2025/11/unleashing-autonomous-ai-agents-why-kubernetes-needs-a-new-standard-for-agent-execution.html)).
- Per-execution cold start breaks the agent's millisecond reason→run→observe loop, which is why
  products invest in warm pools / memory snapshots ([Northflank](https://northflank.com/blog/agent-sandbox-on-kubernetes)).

The declarative-spec visualization choice follows conversational-BI practice (e.g.
[Google Conversational Analytics returns a Vega-Lite spec](https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/render-visualization);
the [MCP viz ecosystem](https://chatforest.com/reviews/data-visualization-mcp-servers/) passes JSON
specs) with a compute (sandbox) / render (spec) split.

## 10. Risks & notes

- **Docker dependency for tests:** the sandbox boundary tests need a Docker daemon; they skip with
  a clear message otherwise (mirrors the Spring-reachable skip). The agent repo still does not
  manage the MCP server or MySQL (see Week 1).
- **ModelScope availability** is unconfirmed; the viz seam makes it non-blocking — confirm
  endpoint/token at implementation, else use the self-hosted declarative renderer.
- **`docker.sock` access** is a privilege surface; mitigated by the §4.2 hardening (one constrained
  container, no network, dropped caps). A remote executor (future) removes it entirely — and is a
  drop-in behind `BaseSandbox`.
- **Container cleanup:** idle TTL + shutdown reaping prevent orphaned containers; Week 3's
  per-session model formalizes this alongside session isolation.
