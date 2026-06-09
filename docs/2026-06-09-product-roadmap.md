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

Detailed notes live in
[2026-06-09-mature-agent-product-research.md](2026-06-09-mature-agent-product-research.md).
Reference links:

- Anthropic: https://www.anthropic.com/engineering/built-multi-agent-research-system
- OpenAI Agents SDK handoffs: https://openai.github.io/openai-agents-python/handoffs/
- Microsoft Copilot Studio multi-agent patterns: https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/architecture/multi-agent-patterns
- Google ADK multi-agent systems: https://adk.dev/agents/multi-agents/
- Salesforce Agentforce guide: https://www.salesforce.com/agentforce/guide
- ServiceNow AI Agents guide: https://servicenow.github.io/sdk/guides/building-ai-agents-guide
- Atlassian Rovo agents: https://support.atlassian.com/rovo/docs/agents/
- Shopify Sidekick: https://help.shopify.com/en/manual/shopify-admin/productivity-tools/sidekick
- GitHub Copilot coding agent: https://docs.github.com/en/copilot/using-github-copilot/coding-agent/about-assigning-tasks-to-copilot
- E2B sandbox contexts: https://e2b.dev/docs/code-interpreting/contexts

## 3. Product Decision Rules

- **Sub-agent rule:** add a sub-agent only for a distinct permission set, tool set, context budget,
  or workflow phase.
- **Write rule:** no write-capable agent is enabled until server-enforced HITL, checkpoint/resume,
  and audit records are in place.
- **Artifact rule:** if the result will be reused, reviewed, approved, downloaded, or rendered, make
  it an artifact with an id and ownership/session metadata.
- **Backend authority rule:** SpringBoot owns business writes, approval validation, actor/session
  binding, and canonical operation hashes.
- **Sandbox rule:** generated code may analyze data and produce artifacts, but it cannot reach
  MySQL, MCP servers, or the public network.
- **UI rule:** the operator console must show enough trace/provenance for a human to trust or reject
  the agent's work.
- **Dependency-bump rule:** run the opt-in live smoke before merging DeepAgents, LangGraph,
  LangChain, MCP adapter, or model-provider upgrades.

## 4. Milestones

### M1. Trusted Read-Only Analysis Workspace

Goal: make the agent useful without granting it write authority.

Status:
- Week 1 foundation is complete.
- Week 2 design targets coordinator + sales-analyst + sandbox + chart artifacts.

Capabilities:
- FastAPI/SSE chat service.
- SpringBoot MCP read tools discovered through `MultiServerMCPClient`.
- Read-only allowlist enforced before tools reach the model.
- Coordinator agent routes to `sales-analyst`.
- `sales-analyst` can call read tools, run sandboxed Python analysis, and generate chart specs.
- Operator-visible tool events for MCP, sandbox execution, and visualization.

Acceptance:
- Default suite passes.
- Real Spring MCP integration passes against MySQL-backed data.
- Real Docker sandbox boundary tests pass or skip clearly when Docker is absent.
- Live smoke: "compare sales by category" calls Spring read tools, runs sandbox code, emits a chart
  artifact/spec, and streams a final answer.

Cut line:
- Do not enable `order-manager` writes here.
- File upload/report generation may be M1.5 if the sandbox path is stable.

### M2. Approved Operational Actions

Goal: let agents propose and execute business actions under explicit human control.

Capabilities:
- `order-manager` sub-agent with scoped read/write tools.
- MongoDB checkpoint/resume for interrupted HITL flows.
- `request_approval` creates server-rendered cards from canonical operation payloads.
- Human approve/reject lives on REST endpoints, never as MCP tools.
- Write tools validate `approval_id`, operation hash, actor/session, expiry, one-time use, and live
  preconditions before execution.
- Audit record links conversation, approval, canonical payload, execution result, and tool trace.

Acceptance:
- Agent can propose a purchase order but cannot execute it before approval.
- Approval card can be approved/rejected from the operator console.
- A changed payload or changed live DB precondition forces a fresh approval.
- One approval cannot be replayed.

Cut line:
- No batch/delete tools until double-confirm + impact preview exists.
- No self-learning skills that affect write behavior.

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
- Model/provider fallback and operational alerts.
- Deployment packaging.
- Optional memory/preferences once permission and audit foundations are in place.

Acceptance:
- A future agent/tool/model change can be reviewed against regression tests and audit expectations.
- Operators can answer "who did what, with which data, under which approval?"

## 5. Near-Term Sequencing

1. **Close Week 1 locally.** Keep the two unpushed review/config commits until the user asks to push.
2. **Before Week 2 implementation, update Week 2 spec if needed** to reflect M1 only:
   coordinator + sales-analyst + sandbox + chart artifact. Keep order-manager in M2.
3. **Build Week 2 in this order:**
   prompts YAML -> agent factory/sub-agent wiring -> sandbox backend boundary -> visualization seam
   -> SSE/tool-event assertions -> live smoke.
4. **After Week 2, decide M1.5 vs M2.**
   If artifacts feel weak, add upload/report. If action workflow is more important, start HITL.

## 6. Deferred Or Stretch Features

- `customer-insight`, `procurement-planner`, `catalog-manager` agents.
- A2A peer-agent integrations.
- Skills/memory auto-learning.
- WebSocket full-chain monitoring beyond SSE tool events.
- PDF export.
- Batch/delete operations.

These are useful only after the product has strong trust, artifact, and approval foundations.
