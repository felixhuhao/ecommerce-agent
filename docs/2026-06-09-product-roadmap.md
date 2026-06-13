# E-Commerce Agent Product Roadmap

> Product-grade roadmap for the e-commerce operations assistant.
> Status: Draft | Date: 2026-06-09
> Parent spec: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)
> Research findings: [2026-06-09-mature-agent-product-research.md](2026-06-09-mature-agent-product-research.md)
> Week 1: [2026-06-08-week1-foundation-design.md](2026-06-08-week1-foundation-design.md)
> Week 2: [2026-06-09-week2-subagents-sandbox-design.md](2026-06-09-week2-subagents-sandbox-design.md)

## 1. Product North Star

Build an extensible commerce operations platform where AI agents can:

- answer operational questions from trusted business data,
- produce inspectable artifacts (charts, reports, approval requests, traces),
- propose actions with clear business impact,
- execute writes only through server-enforced human approval,
- and grow through new tools/agents/connectors without rewriting the core system.

The product is not "a chatbot for e-commerce." It is an operator workspace where conversation is
one interface into governed analysis and action workflows.

## 2. Research Basis

Mature agent products and platforms converge on a few patterns:

| Pattern | Product signals | Design implication |
|---------|-----------------|--------------------|
| Coordinator + specialists | Anthropic's multi-agent research system; OpenAI Agents SDK handoffs; Google ADK multi-agent docs | Use sub-agents for real context/tool/permission boundaries, not cosmetic decomposition. |
| Tool/data boundary | Microsoft Copilot Studio multi-agent guidance; MCP ecosystem; enterprise agent platforms | MCP remains the product's tool/data protocol; business systems stay authoritative. |
| Human review for actions | Shopify Sidekick, Atlassian Rovo, Salesforce Agentforce, ServiceNow AI Agents | HITL and permissioning are core product features, not later polish. |
| Stateful sandbox/artifacts | E2B, GitHub Copilot coding agent style task environments | Persistent per-session sandbox and artifact history are product-grade expectations. |
| Operator visibility | Enterprise copilots show traces, tasks, approvals, and generated assets | The UI must expose agent work, not only final answers. |

Detailed notes and source links live in
[2026-06-09-mature-agent-product-research.md](2026-06-09-mature-agent-product-research.md).

## 3. Product Decision Rules

- **Sub-agent rule:** add a sub-agent only for a distinct permission set, tool set, context budget,
  or workflow phase.
- **Write rule:** the LLM never holds write tools. Agents *propose* via `request_approval`; risky
  writes execute only through a server-enforced, human-approved, **deterministic backend executor**
  keyed by `approval_id`, with audit records in place. No suspended-agent checkpoint/resume is
  required for write safety — the pending action is a durable MySQL `approval_record`.
- **Artifact rule:** if the result will be reused, reviewed, approved, downloaded, or rendered, make
  it an artifact with an id and ownership/session metadata.
- **Backend authority rule:** SpringBoot owns business writes, approval validation, actor/session
  binding, and canonical operation hashes.
- **Sandbox rule:** generated code may analyze data and produce artifacts, but it cannot reach
  MySQL, MCP servers, or the public network.
- **UI rule:** the operator console must show enough trace/provenance for a human to trust or reject
  the agent's work.
- **Trace rule:** own one structured trace stream and project it three ways: SSE/operator timeline,
  developer debugging, and eval/regression detection. LangSmith may be enabled as a dev-only
  side-channel, but product/eval/audit cannot depend on it.
- **Dependency-bump rule:** run the opt-in live reliability harness before merging DeepAgents,
  LangGraph, LangChain, MCP adapter, or model-provider upgrades.

## 4. Milestones

### M1. Trusted Read-Only Analysis Workspace

Goal: make the agent useful without granting it write authority.

Status:
- Week 1 foundation is complete.
- Week 2 design targets a single sales-analyst runtime agent + sandbox + chart artifacts.

Capabilities:
- FastAPI/SSE chat service.
- SpringBoot MCP read tools discovered through `MultiServerMCPClient`.
- Read-only allowlist enforced before tools reach the model.
- M1 runtime uses `sales-analyst` directly; the coordinator/sub-agent seam is deferred until M2
  adds a second specialist with a real routing boundary.
- `sales-analyst` can call read tools, use authoritative `get_statistics` for simple aggregates,
  run sandboxed Python analysis for computations the backend does not own, and generate chart specs.
- The sandbox image includes a small tested `ecommerce_analysis` helper kit so the agent composes
  reliable time-series helpers instead of authoring fragile pandas from scratch on the hero path.
- Operator-visible tool events for MCP, sandbox execution, and visualization.
- On-demand live reliability harness tracks the hero flow pass rate and failure reasons.
- Reusable OTel-shaped trace module captures `astream_events` once; SSE, local debug JSONL, and the
  eval harness project from the same trace record.

Acceptance:
- Default suite passes.
- Real Spring MCP integration passes against MySQL-backed data.
- Real Docker sandbox boundary tests pass or skip clearly when Docker is absent.
- `ecommerce_analysis` helper unit tests pass in the default suite, and the sandbox image can import
  the helper package.
- Hero live run: "Which categories are trending up or down over the last 6 months, forecast next
  month's sales, and chart the result" calls paginated `order_query`, runs sandbox code, emits a
  chart artifact/spec, and streams a final answer.
- The hero run can expand into an N-attempt, RUN_LIVE_LLM-gated reliability harness that reports
  structural pass rate and failure reasons without using an LLM judge or becoming a hard CI gate.
- Trace dumps and eval baseline logs are append-only JSONL with prompt hash, model/settings,
  dependency versions, git commit, pass rate, and failure modes. No M1 trace datastore.
- Simple aggregation questions such as "compare sales by category" prefer `get_statistics` and do
  not force a sandbox hop.

Cut line:
- Do not enable `order-manager` writes here.
- File upload/report generation may be M1.5 if the sandbox path is stable.

### M2. Approved Action Workflow

Goal: let agents *propose* business actions that a human approves and the backend executes — under
explicit human control, with the LLM structurally unable to write.

Capabilities:
- `order-manager` sub-agent with **reads + `request_approval` only** (no write tools in the LLM's
  hands).
- **Propose → approve → execute**, with a durable MySQL `approval_record` as the pending-action
  lifecycle (`pending → approved → consumed`/`rejected`/`expired`/`invalidated`/`failed`). No
  MongoDB checkpoint/resume on the write path.
- `request_approval` creates server-rendered cards from canonical operation payloads.
- Human approve/reject lives on REST endpoints, never as MCP tools; **approve only flips status, it
  does not execute**. Execution is an explicit backend endpoint, e.g.
  `POST /approvals/{approval_id}/execute`.
- One operator Approve click can orchestrate both backend calls in FastAPI: approve, then execute.
  The backend transitions remain separate and auditable.
- **Deterministic backend executor keyed by `approval_id`**: SpringBoot loads the stored canonical
  payload and executes from it in a transaction/row lock (the LLM never re-issues write params),
  validating approval status, hash integrity, actor/session, expiry, one-time use, and live
  preconditions.
- **Idempotent execute + recovery:** replaying an already-consumed approval returns the stored
  `execution_result`; FastAPI uses bounded retry; stale approved-but-unexecuted records have manual
  re-execute and/or sweeper recovery.
- **Server-owned conversation thread:** MongoDB stores appendable messages (`user`, `agent_answer`,
  `agent_proposal`, `approval_status`, `execution_result`) so approval/execution results re-enter
  the same thread without a new LLM turn.
- Audit record links conversation, approval, canonical payload, execution result, and tool trace.
- **Requires the Java companion change** (execute-by-`approval_id`; remove write `@McpTool`s from
  the agent surface) — tracked in §5.

Acceptance:
- Agent can propose a purchase order but holds no tool capable of executing it.
- Approval card can be approved/rejected from the operator console. The Java approve endpoint only
  flips status; the UI presents one human action while FastAPI orchestrates separate approve/execute
  backend events.
- The separate execute call can be retried/idempotently reported.
- Execution completion appends a deterministic result message to the conversation thread; reload
  shows it, and a thin live subscription may push it without refresh.
- A changed live DB precondition between approval and execution forces a fresh approval.
- One approval cannot be replayed or double-spent.

Cut line:
- No batch/delete tools until double-confirm + impact preview exists.
- No self-learning skills that affect write behavior.
- LangGraph `interrupt()`/resume is **not** in scope here; if a mid-conversation pause UX is ever
  wanted, it is polish layered on top of the durable-approval model, never load-bearing for safety.
- Do not build a generic messaging platform. M2 needs persisted append/reload for correctness; live
  per-session SSE/WebSocket push is recommended for demo coherence but can be deferred if needed.

### M3. Operator Console

Goal: make the system usable as a work surface, not only an API.

Capabilities:
- Session list and conversation view.
- Streaming answer panel.
- Tool trace timeline.
- Artifact panel for charts, reports, exported data snippets, and approval cards.
- Approval workspace with impact/diff, expiry, status, and final execution result.
- Health/operator checks for MCP servers, model provider, sandbox, and DB.

Acceptance:
- A human can inspect how an answer was produced.
- A human can approve/reject an action without reading raw logs.
- Generated reports/charts are session-scoped and downloadable/renderable.

Cut line:
- Avoid broad UI polish until trace/artifact/approval surfaces work.

### M4. Product Hardening

Goal: prepare the platform for multi-user, longer-lived operation.

Capabilities:
- Strong session isolation and role-based permissions.
- Audit search and retention policy.
- Prompt/model/tool versioning.
- Evaluation suite for routing, tool choice, approval safety, and answer groundedness.
- ~~Model/provider fallback and operational alerts.~~ **Deprioritized 2026-06-12.** Near-zero
  portfolio value and ongoing cost (a second provider kept in sync *and* eval'd, since a different
  model is different behavior). The demo-reliability R7 cares about is better served by the existing
  pattern — a known-good fallback query + health-gating before demos (the viz seam is the model).
  Revisit only for a real production deployment with a real second provider.
- Deployment packaging.
- ~~Optional memory/preferences once permission and audit foundations are in place.~~ **Cross-session
  memory cut 2026-06-12.** This product's authoritative business state lives in MySQL/Spring and is
  re-queried fresh every turn, so an agent that *remembers* business facts across sessions memorizes a
  stale copy of the source of truth — low value and an R9-style correctness/staleness risk. The
  appealing "what changed since last login" demo is a query over the audit/thread store, not agent
  memory. Within-session memory (M4 slice 2) was the valuable piece. If anything resurfaces here it is
  a small **explicit operator-preferences** record (default period, categories, units) — never
  auto-learned — and only after RBAC/audit land.

Acceptance:
- A future agent/tool/model change can be reviewed against regression tests and audit expectations.
- Operators can answer "who did what, with which data, under which approval?"

## 5. Near-Term Sequencing

1. **Keep local commits local until explicitly pushed.** The branch may be ahead of origin with
   product-roadmap and review commits; do not push without user approval.
2. **Build Week 2 / M1 in this order:**
   prompts YAML -> single analyst agent factory -> dormant coordinator/sub-agent seam -> sandbox
   backend boundary -> visualization seam -> structured trace capture/projection -> structural live
   harness baseline -> `ecommerce_analysis` helper kit + prompt API reference -> helper tests/image
   bake -> SSE/tool-event assertions -> re-run live harness.
3. **After Week 2 / M1, decide M1.5 vs M2.**
   If artifacts feel weak, add upload/report. If the action workflow is more important, start M2
   (Approved Action Workflow).
4. **M2 has a cross-repo prerequisite (sibling `ecommerce-mcp-server`).** Today the Java write tools
   are agent-reachable `@McpTool`s that take `approval_id` + params and rehash incoming params. M2's
   execute-by-`approval_id` model requires a Java companion change: a backend
   `POST /approvals/{approval_id}/execute` path that loads the stored canonical payload, and removal
   of the write `@McpTool`s from the agent-reachable surface. Update the Java spec
   (`docs/2026-06-05-ecommerce-mcp-server-spec.md` §4) before/with M2 implementation. (Not yet
   applied; the Java repo is untouched.)
5. **M2 also has a Python/FastAPI thread prerequisite.** Before wiring the order-manager UI flow,
   add the server-owned conversation thread: append proposal/approval/execution messages, reload by
   session, and optionally push appends live over a per-session SSE/WebSocket stream. This is the
   result re-entry mechanism; do not substitute an LLM follow-up turn for it.

## 6. Deferred Or Stretch Features

- `customer-insight`, `procurement-planner`, `catalog-manager` agents.
- A2A peer-agent integrations.
- ~~Skills/memory auto-learning.~~ **Cut 2026-06-12** (see M4): auto-learned cross-session memory is
  redundant against authoritative MySQL/Spring data and an R9-style staleness risk; at most an explicit
  operator-preferences record later.
- ~~Model/provider fallback.~~ **Deprioritized 2026-06-12** (see M4): known-good fallback query +
  health-gating cover the demo-reliability risk; a second LLM provider is production-only.
- WebSocket full-chain monitoring beyond SSE tool events.
- PDF export.
- Batch/delete operations.

These are useful only after the product has strong trust, artifact, and approval foundations.

## 7. Risks & Mitigations

**Profile note.** The propose→approve→execute redesign (§3 write rule, M2) *retired* the largest
prior risk — the suspended-graph HITL (LangGraph `interrupt()`/resume + MongoDB-checkpoint-for-write
-safety + LLM reproducing write params). It *introduced* one new risk (R8, the approve↔execute
window). Net: the risk profile is better, but shifted.

Ranked by likelihood × impact for a **solo build pursuing both product and portfolio** goals.

| # | Risk | L×I | Mitigation |
|---|------|-----|-----------|
| R1 | **Scope vs solo throughput.** Product framing raises the "done" bar (console, RBAC, audit search, eval, packaging); likely outcome is M1–M2 done well and M3–M4 perpetually in progress, or endless M1 polish that never reaches the crown jewel. | High×High | Hero demo pulls the roadmap; **WIP = 1 milestone** (don't start M2 until M1's hero is solid); **the eval pass-rate is the stop-polishing gate** to move M1→M2; M3/M4 as thin slices; depth over breadth; resist new domain agents/connectors. |
| R2 | **Latency & context/token bloat.** Agent→MCP→sandbox→viz is still a serial path; paginated `order_query` adds hops; product-scale raw rows would bloat context. | Med-High×Med-High | M1 skips coordinator; prefer `get_statistics` for simple aggregates; sandbox only for earned computation; stream tool progress; budget hop count. Scale fix later: backend month×category aggregate. |
| R3 | **Demo non-determinism.** Agent nails the hero flow ~8/10; a 1-in-5 live failure is brutal. | Med×High | Hardened, rehearsed golden path; pre-baked `ecommerce_analysis` helpers; bounded self-debug retry; N-run live harness pass-rate/failure report; known-good fallback query. |
| R4 | **Framework/version drift.** `deepagents 0.6.8`, LangGraph, Spring AI MCP `2.0.0-M8` (pre-release); event shapes / `SubAgent` schema / backend protocol can shift. | High×Med-High | Reproducibility via `uv.lock` (pyproject keeps ranges) + the dependency-bump live reliability harness (§3) as the gate before any upgrade; one thin trace adapter around DeepAgents event shapes. |
| R5 | **Observability + eval blind spot.** Operator traces (M1/M3) are both differentiator and debugging aid; without an eval baseline prompt/model tweaks degrade routing/tool-choice silently. | Med×High | Pull a thin trace+eval slice forward in M1: OTel-shaped ids, per-run tool sequence, sandbox error summary, pass/fail reason, and a JSONL baseline log keyed by prompt hash + deps + model. Latency/token fields and chart-spec validation are **reserved** (schema present; population/validation lands incrementally). Keep semantic judging for M4. |
| R6 | **Agent/model quality.** `deepseek-chat` doing multi-step analysis + helper/glue code + valid chart spec + correct HITL behavior reliably. | Med×High | Exercise the real model on the hero flow early; low temperature for analytical/codegen steps; model is configurable; constrain each specialist's task surface. |
| R7 | **Live-demo dependency fragility.** ModelScope (unconfirmed), DeepSeek (limits/outages), external Java server, MySQL, Mongo — each a break point; "reliability is impressiveness." | Med×High | A real fallback per critical-path dep (viz seam is the model); health-gate before demos; add Mongo only when HITL/continuity needs it. |
| R8 | **Approve↔execute limbo + two-turn coherence** *(new from the redesign)*. A window where an approval is `approved` but `execute` then fails → limbo; and the execution result must re-enter the conversation the user is watching. | Med×Med-High | Idempotent executor returns stored `execution_result`; bounded retry + stale-approved recovery; server-owned Mongo conversation thread appends deterministic approval/execution-result messages; thin live push for demo coherence. |
| R9 | **Agent numbers vs authoritative `get_statistics`.** Self-computed pandas aggregates can mis-join or use the wrong status filter and disagree with canonical stats — confidently wrong. | Med×Med-High | Route headline figures through `get_statistics`; use the sandbox only for derivations stats don't cover; prompt prefers authoritative tools. |
| R10 | **Sandbox robustness/security.** `docker.sock` ≈ host root; container leaks/zombies, timeout-not-killing, fork pressure, WSL2 quirks. | Med×Med-High | Dedicated sandbox test matrix (network-isolation + timeout guards land in M1); an idle-time hook now, with the reaper/concurrency cap arriving with the M2/M4 per-session model; the `BaseSandbox` seam lets a managed/remote executor remove the `docker.sock` privilege later. |
| R11 | **Prompt injection via business data.** Review/product/customer text can carry instructions; the agent can call `request_approval`. | Med×Med | Human-approval gate is the backstop; `--network none` blocks sandbox exfil; frame untrusted data as data, treat review/customer text as tainted. |
| R12 | **Audit/artifact schema lock-in.** Must "survive future approved operations + multi-user permissions"; getting it wrong forces migration. | Med×Med | Design the *minimal* audit/artifact schema deliberately before M2 writes, even if thin. |
| R13 | **Two-repo coordination / regressing the "done" Java server.** The execute-by-`approval_id` companion change reopens tested approval enforcement; two specs + two test suites to keep in sync. | Med×Med | Treat the Java change as its own reviewed slice; re-run the negative-case matrix; keep the two specs cross-linked. |

**Top to watch:** R1 (throughput) and R3 (demo reliability) for the portfolio goal; R8 (approve↔execute) for the product goal; R5 (no eval/trace) is the silent one that makes the rest harder to catch.
