# Mature Agent Product Research Findings

> Research notes for reframing the e-commerce agent from interview demo to extensible product.
> Status: Draft | Date: 2026-06-09
> Parent spec: [2026-05-25-ecommerce-agent-design.md](2026-05-25-ecommerce-agent-design.md)
> Product roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md)

## 1. Scope

This research pass looked at mature or product-facing agent systems, especially systems with
sub-agent, handoff, tool, approval, sandbox, artifact, and operator-observability patterns.

The goal is not to clone any one product. The goal is to extract product-shaping decisions for this
commerce operations assistant:

- when sub-agents are worth adding,
- where business authority should live,
- how risky actions should be approved,
- how analysis/artifacts should surface,
- and what should be deferred until trust and audit foundations exist.

## 2. Executive Findings

1. **Sub-agents are a product boundary, not a badge.** Mature systems use specialists for separate
   context windows, tools, permissions, or workflow phases. Cosmetic agent splitting creates
   orchestration cost without product value.
2. **Coordinator + specialist is mainstream.** Anthropic, OpenAI, Google ADK, Salesforce, Rovo,
   and DeepAgents all support some form of coordinator, handoff, or sub-agent composition.
3. **Tool access and data access are control-plane concerns.** Microsoft explicitly recommends MCP
   for secure, authenticated tool/data access; ServiceNow separates who can invoke an agent from
   the identity/data scope it runs under.
4. **Human review is a core commerce/admin pattern.** Shopify Sidekick presents changes for review;
   Salesforce actions can require confirmation; Rovo tools run with user permission. For this
   product, HITL is not polish.
5. **Artifacts and traces matter as much as chat.** GitHub Copilot cloud agent turns work into
   branches, commits, logs, and pull requests. OpenAI Agents SDK emphasizes traces across model
   calls, tools, handoffs, and guardrails. Operators need inspectable work products.
6. **Stateful isolated execution is a mature pattern.** E2B and GitHub both point toward isolated
   task environments. For us, a persistent per-session Docker sandbox is directionally correct.
7. **Declarative visualization specs are the right seam.** Google Conversational Analytics returns
   chart specifications such as Vega-Lite; this supports our compute/render split.
8. **Memory and self-learning should trail governance.** Products expose memory/skills, but for a
   business-operations system they should arrive after permissioning, audit, and trace surfaces.

## 3. Product Pattern Table

| Product / platform | Observed pattern | Implication for this project |
|--------------------|------------------|------------------------------|
| Anthropic Research | Lead agent decomposes research into parallel subagents, each with separate context and exploration path; Anthropic also warns multi-agent systems use substantially more tokens and are best for high-value, broad tasks. | Use sub-agents where they buy context isolation or parallel breadth. Avoid splitting simple sequential workflows. |
| OpenAI Agents SDK | Agents have tools, handoffs, guardrails, sessions, streaming, tracing, and human-review paths. Handoffs delegate to specialist agents. | Our coordinator -> specialist structure is normal. We should keep explicit traces and live reliability gates because SDK/runtime event shapes matter. |
| Microsoft agent architecture | Recommends platform-native orchestration for internal subagents, MCP for secure tool/data access, A2A for cross-platform peer agents, least privilege, auditability, typed payloads, and governance. | MCP is the correct tool/data protocol now. A2A is future-facing only when we integrate external peer agents. |
| Google ADK | Frames mature agent applications as multi-agent, multi-node workflows, mixing AI agents with deterministic graph/workflow nodes for predictability and reliability. | For HITL and writes, do not rely on agent-only loops. Use deterministic backend approval and execution nodes. |
| Salesforce Agentforce | Subagents own their actions; actions can be deterministic or exposed to the LLM as tools; action metadata includes confirmation and output/context controls. | Give each sub-agent its own tool/action set. Do not share write-capable actions broadly. Treat confirmation as action metadata/policy, not just prompt text. |
| ServiceNow AI Agents | Separates `securityAcl` from execution identity/data access; recommends built-in/reference tools before scripts; supports MCP among many tool types. | Keep trusted user/session identity in headers/context. Keep Java business services authoritative; use generated scripts only for sandbox analysis. |
| Atlassian Rovo | Agents have specialized objectives, knowledge sources, tools, permissions/governance, usage tracking, subagents, and automation integrations. | Product needs agent registry/governance eventually, but first needs visible tool traces and permission boundaries. |
| Shopify Sidekick | Commerce assistant works in store context, can analyze/manage/admin tasks, works in the background for longer tasks, and presents changes for review before applying them. | This is the closest commerce-product signal: analysis and admin action are both valuable, but changes need review before execution. |
| GitHub Copilot cloud agent | Agent works in an isolated GitHub Actions environment, plans/changes/tests, and makes work visible through branches, commits, logs, and pull requests. | Treat agent work as artifacts and audit trail. For commerce, equivalents are chart specs, reports, approval records, diffs, and execution logs. |
| E2B Code Interpreter | Supports sandbox code execution contexts with create/list/restart/remove operations and per-context execution. | A persistent per-session sandbox with lifecycle controls and preinstalled analysis helpers is more product-like than a fresh throwaway process per call. |
| LangChain DeepAgents | Subagents help keep main context clean and provide specialized instructions/tools. DeepAgents supports custom subagents and compiled graphs. | Keep the sub-agent seam, but activate it only when a second specialist creates a real routing/context boundary. |
| Google Conversational Analytics | Agent responses can include chart specifications, rendered separately with Vega-Lite/Altair. | Keep visualization as a declarative chart-spec artifact; UI rendering is separate from analysis computation. |

## 4. Design Implications

### 4.1 Sub-Agent Strategy

Keep the sub-agent list short and permission-driven:

- **Now:** run `sales-analyst` directly as the M1 runtime specialist; it has a distinct prompt/tool
  set, but no coordinator is needed while there is only one specialist.
- **Next:** enable coordinator + sub-agents when `order-manager` lands with `request_approval`,
  deterministic execute-by-`approval_id`, and audit records. LangGraph checkpoint/resume is not
  required for write safety.
- **Later:** `customer-insight`, `procurement-planner`, or `catalog-manager` only when each has
  dedicated tools, permissions, and artifacts.

Do not add sub-agents for basic routing, greetings, or small prompt variations.

### 4.2 Tool And Business Authority

Mature products separate agent reasoning from business authority. For this project:

- SpringBoot owns database writes, business rules, approval records, canonical payloads, and
  operation hashes.
- Python owns orchestration, model calls, sandboxed analysis, chart/report artifact creation, and
  streaming.
- MCP is the tool/data protocol. REST is reserved for human approval transitions and product APIs.
- The LLM never controls trusted identity; user/session/service identity is injected by the app
  boundary and validated by SpringBoot.

### 4.3 HITL And Approval

The commerce/admin product analogy is strongest here:

- Read operations are low risk and can execute directly.
- Write proposals require server-rendered approval cards.
- Approval must not be an MCP tool.
- Approved writes should execute through a deterministic backend endpoint keyed by `approval_id`,
  loading the stored canonical payload rather than accepting write params from the LLM.
- Approved writes must be one-time use, actor/session-bound, expiring, hash-validated,
  transaction/lock-protected, and rechecked against live preconditions.
- Batch/delete operations need a higher approval tier and should stay deferred.

### 4.4 Artifacts And Operator Console

The product should not be a chat transcript with hidden side effects. It should expose:

- chart specs and rendered charts,
- generated reports,
- approval cards and action diffs,
- tool trace timelines,
- sandbox execution logs/summaries,
- dependency health checks,
- and audit ids linking conversation -> tool calls -> approvals -> execution results.

This is the operator-console version of GitHub's PR/log/commit model for coding agents.

### 4.5 Sandbox

The sandbox should be:

- persistent per session,
- network-isolated,
- resource-limited,
- file/artifact oriented,
- equipped with a small tested commerce-analysis helper package,
- and disposable/reapable by TTL or session close.

The current Week 2 DockerSandbox plan is compatible with this. A managed sandbox can replace it
later behind the backend seam. Helpers should parse files the agent wrote into `/workspace`, never
fetch from the business backend directly.

### 4.6 Visualization

Use declarative specs as the durable artifact:

- sandbox computes aggregates,
- visualization tool returns chart spec,
- operator console renders and stores/display-links the artifact.

This avoids coupling business analysis to a single UI renderer.

### 4.7 Memory And Skills

Memory and skills are not rejected, but they should be sequenced carefully:

- user preferences can land after session isolation is stable,
- skills should be reviewable and scoped,
- skill use should be auditable,
- self-learning should not affect write behavior until governance exists.

## 5. Roadmap Consequences

1. **Week 2 / M1 should stay read-only.** Build the direct sales-analyst runtime agent + sandbox +
   chart artifact. Do not sneak in coordinator latency or order-manager writes.
   Pull forward a thin reliability loop: run the live structural harness once as a baseline, add the
   tested `ecommerce_analysis` helpers, then re-run and compare failure modes.
2. **M1.5 is artifact depth.** File upload and Markdown reports are useful if the sandbox path
   lands cleanly.
3. **M2 is approved action workflow.** Order-manager, `request_approval`,
   execute-by-`approval_id`, approval cards, and audit trail should land together. Checkpoints are
   for conversation continuity, not write safety.
4. **M3 is operator console.** Traces, artifacts, approvals, and health surfaces are core product
   UX, not late polish.
5. **M4 is hardening.** Multi-user permissions, prompt/model/tool versioning, evaluation suite,
   provider fallback, and deployment packaging.

## 6. Open Questions

- Should chart/report artifacts be stored first in sandbox metadata, MongoDB, or a dedicated
  artifact table/service?
- How much tool trace detail should the UI show to operators versus developers?
- Should the first operator console be built before or after HITL, given that approval cards need a
  UI anyway?
- Should the sandbox support persistent Python kernel state in M1.5, or is filesystem state enough?
- What is the minimum audit schema that can survive future approved operations and multi-user
  permissions?
- Do we need a formal agent/tool registry before adding the third domain agent?

## 7. Sources

- Anthropic, "How we built our multi-agent research system":
  https://www.anthropic.com/engineering/multi-agent-research-system
- OpenAI Agents SDK overview:
  https://developers.openai.com/api/docs/guides/agents
- OpenAI Agents SDK agent definitions and handoffs:
  https://openai.github.io/openai-agents-python/agents/
- OpenAI Agents SDK tracing:
  https://openai.github.io/openai-agents-python/tracing/
- Microsoft multi-agent patterns:
  https://learn.microsoft.com/en-us/agents/architecture/multi-agent-patterns
- Google ADK workflows:
  https://adk.dev/workflows/
- Salesforce Agentforce actions:
  https://developer.salesforce.com/docs/ai/agentforce/guide/ascript-ref-actions.html
- ServiceNow AI Agents guide:
  https://servicenow.github.io/sdk/guides/building-ai-agents-guide
- Atlassian Rovo agents:
  https://support.atlassian.com/rovo/docs/agents/
- Shopify Sidekick:
  https://help.shopify.com/en/manual/shopify-admin/productivity-tools/sidekick
- GitHub Copilot cloud agent:
  https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent
- E2B code contexts:
  https://e2b.dev/docs/code-interpreting/contexts
- LangChain DeepAgents subagents:
  https://docs.langchain.com/oss/python/deepagents/subagents
- Google Conversational Analytics visualization:
  https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/render-visualization
