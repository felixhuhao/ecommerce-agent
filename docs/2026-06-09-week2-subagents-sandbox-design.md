# Week 2: Analyst Agent + Sandbox — Design Spec

> The analysis-and-charting path: a single read-only sales-analyst agent runs isolated code in a
> Docker-backed sandbox and emits chart specs. Coordinator/sub-agent wiring remains a seam, activated
> when M2 introduces a second specialist with a real routing boundary.
> Status: Draft | Date: 2026-06-09
> Product milestone: M1 — Trusted Read-Only Analysis Workspace
> Parent spec: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)
> Product roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md)
> Builds on: [2026-06-08-week1-foundation-design.md](2026-06-08-week1-foundation-design.md)
> Server contract: [ecommerce-mcp-server spec](../../ecommerce-mcp-server/docs/2026-06-05-ecommerce-mcp-server-spec.md)

## 1. Scope

Week 1 delivered a single agent + FastAPI/SSE + the SpringBoot MCP read tools. Week 2 builds the
**read-only analysis-and-charting path end to end**: a single **sales-analyst** deep agent that
queries business data, runs isolated Python in a Docker-backed sandbox when computation earns it,
and produces a chart spec via the ModelScope visualization MCP.

Week 2 is the implementation slice for **Milestone 1 (M1): Trusted Read-Only Analysis Workspace**.
Milestones are the canonical roadmap vocabulary; week labels describe implementation slices only.

**In scope (Week 2 / M1):**
- **sales-analyst** runtime agent (read-only): the 10 SpringBoot read tools +
  `generate_visualization`, backed by `DockerSandbox`
- **Coordinator/sub-agent seam** only: factory shape exists, but M1 does not route through a
  coordinator until M2 adds `order-manager`
- **DockerSandbox** — a custom DeepAgents backend giving isolated code execution + a sandbox filesystem
- **Pre-baked `ecommerce_analysis` helper kit** in the sandbox image — four stable commerce
  analysis helpers, used by agent-written glue code instead of fresh pandas from scratch
- **Visualization** via ModelScope MCP `generate_visualization`, behind a swappable seam
- **YAML prompt management** (migrate the inline Week 1 prompt)
- Two-tier tests (default boundary tests incl. helper + real-Docker sandbox tests; opt-in live
  reliability harness)

**Deferred by milestone:**
- **M1.5 artifact depth:** file upload (`POST /api/upload`) + `read_uploaded_file` +
  `write_report` — sandbox-only, can land after the sandbox path is stable; no HITL needed.
- **M2 approved action workflow:** `order-manager` sub-agent with **reads + `request_approval`
  only** (no write tools in the LLM's hands). Propose → human-approve (REST) → **deterministic
  backend executor keyed by `approval_id`**; the pending action is a durable MySQL `approval_record`
  (no LangGraph interrupt/resume, no MongoDB checkpoint for write safety). Requires the Java
  companion change (execute-by-`approval_id`; parent §5.2).
- **M4 product hardening:** skills/memory middleware, `web_search`, `assign_skill`, preferences,
  and long-lived memory — requires governance, session isolation, and audit policy.

**Deferred infrastructure:** MongoDB checkpoint (conversation continuity only, *not* write safety),
`ContextVar` session isolation, and `CompositeBackend` routing for `/memories` + `/skills` land when
M2/M4 requires them, not in M1.

**Out of scope:** the operator console milestone — Week 2 verifies a chart *spec* is produced;
rendering belongs to the UI/artifact surface later.

## 2. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Runtime agent | **Single sales-analyst deep agent** for M1 | With only one specialist, a coordinator adds serial model calls without a real routing decision. |
| Coordinator seam | DeepAgents native `subagents` (`SubAgent` dicts via a factory), enabled at M2 | First-class framework feature; each sub-agent declares its own `tools`/`skills`/`interrupt_on`. |
| Aggregation rule | Simple aggregation → authoritative Spring stats; sandbox only for computation stats do not own | Avoids latency and avoids pandas disagreeing with canonical `get_statistics`. |
| Analysis helpers | Pre-baked `ecommerce_analysis` package, capped at ~4 stable functions | Mature code-interpreter products are reliable because agents compose reliable building blocks instead of inventing fragile pandas every run. |
| Reliability harness | RUN_LIVE_LLM-gated N-run structural eval with trace/failure capture | Measures the hero path before and after helper/prompt changes; no LLM-as-judge and no per-commit token burn. |
| Exec backend | Custom `DockerSandbox(BaseSandbox)` | Self-hosted isolation, no SaaS; conforms to DeepAgents' backend protocol; swappable for a remote executor later. |
| Sandbox lifecycle | **Persistent per-session container**, reused across `execute` calls | Matches mature code-interpreters (§9): statefulness + no per-call cold start. |
| Statefulness | **Filesystem-stateful** (files persist; each `execute` runs fresh code) | Agent round-trips data through sandbox files; simplest model that fits DeepAgents' shell-based `execute`. Full REPL/kernel state is a later upgrade. |
| Files location | **On the sandbox** (backend = sandbox) | All file tools + `execute` share one workspace; code consumes the files the agent writes. Matches parent §2.2. |
| Visualization | ModelScope MCP `generate_visualization` (declarative, 26→1) **behind a seam** | Conversational-BI products use declarative specs rendered in the UI; compute/render split. Self-hosted renderer is a drop-in if ModelScope is unavailable. |
| Prompts | YAML (`prompts/prompts.yml` + loader) | Parent §4.5; keeps agent definitions thin (config, not prose). |
| Structure | Option C: `sandbox/` package; `agents.py`; `prompts/`; viz seam in `mcp_client.py` | Pre-split only the certain-to-grow, multi-concern piece (sandbox); keep the rest flat with clean seams. |
| Deferred features | M1.5/M2/M4 features deferred with **proven seams** | They are native additive slots (`tools`/`skills`/`interrupt_on`); `build_agent` threads all of them now. |

## 3. Architecture

### 3.1 Project structure (option C)

```
src/ecommerce_agent/
├── config.py            # + sandbox settings (image, mem/cpu/pids caps, timeout, idle TTL)
├── models.py            # unchanged
├── mcp_client.py        # + ModelScope connection enable + viz-tool allowlist seam
├── agent.py             # build_agent(model, *, tools, subagents, middleware, skills, backend)
├── agents.py            # NEW: sales-analyst factory + dormant coordinator/sub-agent seam
├── prompts/
│   ├── prompts.yml      # NEW: sales_analyst prompt (+ optional coordinator prompt for M2)
│   ├── analysis_helpers.md # NEW: compact helper API reference included/linked from the prompt
│   └── loader.py        # NEW: tiny typed YAML loader (read once at build)
├── sandbox/             # NEW package (the one certain-to-grow, multi-concern piece)
│   ├── __init__.py      # exports DockerSandbox
│   ├── backend.py       # DockerSandbox(BaseSandbox): execute() + upload_files() + lifecycle
│   └── config.py        # container hardening flags / resource limits builder
├── sandbox_image/
│   ├── Dockerfile.sandbox
│   └── ecommerce_analysis/ # NEW: helper package copied/baked into the sandbox image
└── api/
    ├── app.py           # lifespan builds the sandbox backend + wires it into the agent
    └── chat.py          # unchanged request/SSE contract
```

No `session/`, custom `middleware/`, or `checkpoint/` modules yet — those are M2/M4 additions.

### 3.2 Agent composition

- **`build_agent(model, *, tools, subagents, middleware, skills, backend)`** — every DeepAgents
  extension slot is a parameter from day one (proven seams). Week 2 / M1 passes:
  `subagents=[]`, `skills=[]`, the 10 SpringBoot read tools, `generate_visualization`, and the
  `DockerSandbox` backend.
- **sales-analyst runtime agent:** `agents.py` builds the M1 agent directly with a prompt from
  `prompts.yml:sales_analyst`. It has read-only SpringBoot tools (reuse Week 1's
  `READ_ONLY_SPRING_TOOLS`) **+** `generate_visualization`; `execute` + file tools come from the
  shared backend (no per-tool wiring).
- **Dormant coordinator seam:** `agents.py` may expose a `build_coordinator_agent` /
  `build_sales_analyst_subagent` shape, but M1 does not put the analyst behind `subagents=[...]`.
  M2 activates this once `subagents=[sales_analyst, order_manager]` gives the coordinator a real
  routing decision.

### 3.3 Documented insertion points (no rework later)

| Future capability | Milestone | Slot | Where |
|-------------------|-----------|------|-------|
| file upload / reports | M1.5 artifact depth | sandbox upload/read/report tools | `sandbox/`, product API, artifact seam |
| `web_search` | M4 product hardening | coordinator or analyst `tools` | append one `BaseTool` |
| order-manager | M2 approved actions | a `SubAgent` with **reads + `request_approval` only** (no write tools, no `interrupt_on`); writes run in a deterministic backend executor by `approval_id` | `agents.py` factory + `subagents`; executor + Java companion change |
| skills / `assign_skill` | M4 product hardening | `skills=` + skills middleware | `build_agent` params |
| memory | M4 product hardening | `middleware=` + `CompositeBackend` | `build_agent` params |

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
- **M1:** a single container (one session). **M2/M4:** one container per `session_id`
  ("singleton per session"). Warm pools / memory snapshots are a future scaling technique, not an
  immediate product need.

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
`python + pandas + numpy + ecommerce_analysis` baked in:
`Dockerfile.sandbox` → `ecommerce-agent-sandbox:dev`.

Helper/package changes mean a sandbox image rebuild. In development, mounting the helper source into
the container is acceptable for fast iteration; for demos and CI images, bake the exact helper
version into the image so the agent sees the same API every run.

### 4.4 Files-on-sandbox & the data path

The DeepAgents backend *is* the sandbox, so the agent's file tools and `execute` share one
workspace. MCP query **results return to the agent's context** (text); when the agent wants to
analyze them, it `write_file`s them into `/workspace` and sandboxed code consumes them there —
files used by code never touch the host (parent §2.2). Week 2 / M1 uses a single `DockerSandbox`
backend; M4 wraps it in a `CompositeBackend` that keeps `/workspace` on the sandbox while
routing `/memories` and `/skills` to their own backends (parent §12).

### 4.5 Pre-baked commerce analysis helpers

M1 includes a tiny, source-controlled Python package baked into the sandbox image:
`ecommerce_analysis`. It is a reliability tool, not a demo script. The agent still fetches data,
chooses the analysis path, writes glue code, and interprets results; the helper kit removes the
most fragile from-scratch pandas pieces from the golden path.

Stable API contract:

```python
load_orders_df(path) -> pandas.DataFrame
monthly_sales_by_category(orders_df) -> pandas.DataFrame
simple_forecast(monthly_df, periods=1) -> pandas.DataFrame
validate_forecast_result(forecast_df) -> None
```

Rules:
- `load_orders_df(path)` only parses an order JSON/CSV file already written by the agent into
  `/workspace`; it never fetches data and never reaches the network. The fetch boundary remains:
  MCP/SpringBoot fetches, the agent writes the result file, the sandbox parses and computes.
- Keep the helper kit small, roughly these four functions. More helpers become M4 skills/library
  governance, not Week 2 scope.
- Signatures are stable contracts the prompt depends on. If a signature changes, update the compact
  `prompts/analysis_helpers.md` reference and helper tests in the same change.
- Helpers are deterministic and unit-tested in the default suite. The whole point is to move
  fragile codegen into fixable code that CI can catch.
- Scope honesty: helpers harden the rehearsed hero path. Off-path improv questions may still need
  fresh agent-written code, so the live demo should lead with the golden path before exploratory
  questions.

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
- **Artifact storage deferral:** M1 may stream the chart spec directly in the response. Assigning
  durable artifact ids, ownership/session metadata, and artifact storage is an M1.5/M3 concern,
  not an overlooked Week 2 requirement.

## 6. Prompts (YAML)

`prompts/prompts.yml` holds the `sales_analyst` prompt, loaded once by `prompts/loader.py` at agent
build. Week 1's inline `SYSTEM_PROMPT` migrates here. A `main_agent`/coordinator prompt can live in
the same file as a dormant M2 seam, but M1 does not route through it.

The sales analyst prompt points to a compact helper API reference
(`prompts/analysis_helpers.md`) and says: for commerce time-series analysis, prefer
`ecommerce_analysis` helpers before writing custom pandas. It also states the data boundary:
helpers read files in `/workspace`; they do not fetch from SpringBoot or the network.

## 7. Data flow (the Week 2 demo)

"Which categories are trending up or down over the last 6 months, forecast next month's sales, and
chart the result":

```
POST /api/chat/stream {message}
 → sales-analyst calls order_query pages for the last 6 months (SpringBoot MCP) → data into context
 → write_file(orders.json) [sandbox]
 → execute(glue code using ecommerce_analysis: load, bucket month×category, forecast) [sandbox]
 → generate_visualization(spec from aggregates) [ModelScope MCP] → chart spec
 → sales-analyst streams analysis + chart spec
```

SSE frames are unchanged (`token` / `tool` / `done` / `error`); `tool` frames now also surface
`execute` and `generate_visualization`, so the boundary is observable in tests.

For simpler aggregation questions such as "compare sales by category," the agent should prefer
authoritative SpringBoot statistics (`get_statistics`) and skip the sandbox unless the user asks for
analysis the backend does not already own. The forecast hero is intentionally illustrative: six
monthly points are enough to demonstrate the workflow, not enough to claim rigorous forecasting.

## 8. Testing & acceptance

Carries Week 1's two-tier shape.

**Default boundary tests (deterministic, no LLM):**
- `ecommerce_analysis` helper unit tests: parse representative `order_query` result files; bucket
  month×category; produce finite one-period forecasts; reject invalid/empty forecast outputs.
- `sandbox/` against **real Docker**: `execute` runs code; `--network none` enforced (a network
  call fails); timeout + resource caps respected; files persist across `execute` calls in one
  session; container torn down. **Skips cleanly when Docker is absent** (like the Spring-reachable
  skip).
- sandbox image smoke: `python -c "import ecommerce_analysis"` succeeds in the built image.
- `agents.py` / `build_agent`: the M1 analyst factory wires the right tool allowlists; dormant
  coordinator/sub-agent seams exist without being on the hot path; all extension slots threaded.
- viz allowlist + ModelScope connection registry (mirrors the Spring allowlist tests).
- prompt loader.

**Opt-in live reliability harness (`RUN_LIVE_LLM=1`):** parameterized live run for the 6-month
category trend/forecast hero. Run it on demand before demos, and after prompt/model/helper changes
or dependency bumps (DeepAgents/LangGraph/LangChain/MCP adapters). Keep it out of default pytest and
per-commit CI; full multi-hop runs cost minutes and model tokens.

The harness runs `N` attempts (e.g. `LIVE_EVAL_RUNS=5` or `10`) and reports pass rate plus failure
reasons. Treat pass rate as a tracked metric, not a hard gate at small N. Structural assertions:
- `order_query` is called and paginated as needed.
- no write/approval tools appear.
- `execute` is called and either succeeds or performs a bounded self-debug retry before failing.
- forecast outputs are non-empty finite numbers.
- `generate_visualization` is called with a schema-valid chart spec; if validation fails, the
  renderer seam can return a default-chart fallback and record the degradation.
- the stream completes with `done`, not `error`.

Each live run writes a small structured trace: run id, model/settings, tool sequence, tool latencies,
sandbox stdout/stderr summary, chart validation result, pass/fail assert, and failure reason. This
is the first thin slice of R5 observability, not a separate product console.

Recommended hardening order: implement the harness early and run it once against the from-scratch
codegen baseline, then add `ecommerce_analysis` + prompt reference and re-run. That proves the
helper kit earned its place instead of tuning by vibes.

**Acceptance (definition of done):**
- sales-analyst runs directly in M1; coordinator/sub-agent routing is present only as a dormant seam.
- sales-analyst analyzes seeded order data in the sandbox and emits a chart spec via ModelScope (or the
  seam's renderer).
- `ecommerce_analysis` helpers are documented, baked into the sandbox image, and tested in the
  default suite.
- The on-demand live reliability harness produces a pass-rate/failure-reason report.
- Default suite green, including the real-Docker sandbox boundary tests (skipped if no Docker).
- A rehearsed single-run hero path passes by hand before demo; the N-run harness records the broader
  reliability metric.

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
- **Pagination is the residual data cost:** the hero can use 2-3 `order_query` pages on the seed
  data. If that starts to feel heavy at product scale, the scale fix is a SpringBoot
  month×category aggregate endpoint that hands the sandbox a compact series for forecasting. Do not
  build that Java change in M1.
- **`docker.sock` access** is a privilege surface; mitigated by the §4.2 hardening (one constrained
  container, no network, dropped caps). A remote executor (future) removes it entirely — and is a
  drop-in behind `BaseSandbox`.
- **Container cleanup:** idle TTL + shutdown reaping prevent orphaned containers; M2/M4's
  per-session model formalizes this alongside session isolation.
