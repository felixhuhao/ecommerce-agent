# Feature Gap Analysis — Post-M4 (Round 2 Research)

> Second mature-product research pass, after M1–M4 are complete. Goal: find the next slice(s)
> genuinely worth building by comparing the project to the current category and rating candidates
> by value vs cost.
> Status: Draft | Date: 2026-06-13
> Baseline: [2026-06-09-mature-agent-product-research.md](2026-06-09-mature-agent-product-research.md)
> Roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md)

## 1. Method

The first research pass (2026-06-09) was **architecture**-focused (sub-agents, HITL, sandbox,
trace). M1–M4 implemented that architecture. This pass is **feature**-focused: where does the product
fall short of mature peers, and which gap is worth closing next?

- **Comparison set (same category, not generic chatbots):** operator/copilot workspaces over governed
  business data — Shopify Sidekick, Salesforce Agentforce, Atlassian Rovo, Microsoft Copilot Studio —
  and conversational-analytics agents (Google Conversational Analytics / Looker, ThoughtSpot, Hex/Julius).
- **Gaps are mapped by dimension** (§3), not as a flat wishlist.
- **Candidates are scored** (§4) on six axes, with two project-specific guardrails:
  - **Penalize breadth.** Gap analysis naturally suggests "more connectors / more domain agents."
    That cuts against the roadmap's depth-over-breadth discipline (R1), so me-too breadth scores low
    by rule, not by accident.
  - **Reward leverage + demoability.** Features that light up existing work and produce a visible demo
    serve both the product and portfolio goals.

## 2. Executive findings — what the category did in 2025–26

1. **The big shift is reactive → proactive.** The marquee 2026 capability across peers is an agent
   that *watches the business in the background* and surfaces insight before being asked. Shopify
   **Sidekick Pulse** runs in the background monitoring sales/inventory/customer/marketing and alerts
   on anomalies, trends, and optimization opportunities. Agentforce fires **anomaly alerts on
   behavioral drift** and drives proactive outreach. Conversational-analytics agents now "continuously
   explore data, detect anomalies, and summarize changes as they happen instead of waiting for
   scheduled reports."
2. **Anomaly detection now explains cause, not just flags outliers.** "AI agents go beyond traditional
   anomaly detection that only flags outliers by explaining impact and cause."
3. **Trust = "show your work."** Mature analytics agents attach **inline citations** to answers (which
   systems/queries/rows produced a number), **confidence scoring**, and a tamper-proof audit log.
   Nearly half of users verify an AI answer before trusting it — provenance is now table stakes.
4. **Agent observability became an operator surface.** Agentforce Observability ships agent analytics,
   **health monitoring of "silent failures,"** drift alerting, configurable thresholds → notifications,
   and session-trace drill-down. The eval/trace plumbing we built internally is the productized version
   of this.
5. **Scheduled KPI digests** are a standard lighter-weight proactive feature (recurring summaries
   delivered without a prompt).

## 3. Gap map by dimension

| Dimension | What mature peers do | What we have (M1–M4) | Gap |
|---|---|---|---|
| **Proactivity & monitoring** | Background watcher; anomaly + cause; alerts; scheduled digests; "before you ask" | Purely **reactive** — every insight requires an operator turn | **Large** — this is the dominant category gap |
| **Analytical depth** | Forecasting + anomaly detection first-class; explain impact/cause; drill-down follow-ups | Sandbox forecasting on the hero path; within-session memory enables follow-ups (slice 2) | **Medium** — anomaly detection + cause; richer drill-down |
| **Trust / answer grounding** | Inline citations (query/rows), confidence scoring, provenance, "show your work" | Tool-trace timeline + audit spine; `get_statistics` authority; tool-choice eval guards the authoritative path | **Medium-high** — no *answer-level* citation/confidence; groundedness judge still deferred. Directly tied to R9 (confidently-wrong numbers) |
| **Agent observability (operator-facing)** | Health/silent-failure monitoring, drift alerts, pass-rate analytics, threshold notifications | Internal eval baselines (JSONL) + live reliability harness + trace store; **no operator-facing health surface** | **Medium** — productize what we already capture |
| **Multi-user & collaboration** | Roles, governance, usage tracking, shared workspaces | Auth + RBAC + isolation + audit + retention (slice 5, just landed) | **Small** — current here; sharing/collab is a stretch |
| **Artifacts / reporting** | Generated reports, exports (e.g. ShopifyQL reports) | Chart-spec artifacts + artifacts panel | **Medium** — report generation/export (old M1.5) |
| **Connectors / domain agents** | Many connectors; agent/tool registries | Single Spring MCP + ModelScope viz | **Large but OUT OF STRATEGY** — breadth; R1 says resist |
| **Lifecycle (versioning / deploy)** | Prompt/agent versioning; managed deploy | `docker-compose.yml` exists; no versioning | **Medium but low portfolio value** (invisible in a demo) |

## 4. Candidate scoring

Axes: **P** = portfolio impressiveness · **V** = product value · **C** = cost/effort (lower is better,
so "Low" is good) · **R** = risk · **L** = leverage on existing work · **D** = demoability. H/M/L.

| # | Candidate | P | V | C | R | L | D | Verdict |
|---|-----------|---|---|---|---|---|---|---------|
| **A** | **Proactive monitor + anomaly alerts** ("Pulse"): background watcher runs periodic checks via `get_statistics`/read tools + sandbox, detects anomalies (sales drop, stockout, margin/return shifts), explains likely cause, surfaces alerts into the console/digest | **H** | **H** | H | M-H | **H** | **H** | **Top pick** — marquee 2026 capability, huge demo, deep (not broad), high reuse; cost & alert-noise are design problems, not blockers |
| **B** | **Answer grounding + confidence** ("show your work"): every headline number carries an inline citation to the tool call/query/rows that produced it + an authority/confidence badge (`get_statistics` vs sandbox-computed); optional groundedness LLM-judge eval | **H** | **H** | M | M | **VH** | **H** | **Strong #2 / foundational** — cheaper than A, very high reuse, *directly retires R9*; less "wow" but more defensible. De-risks A |
| **C** | **Scheduled KPI digests**: operator schedules a recurring summary; agent generates a report artifact + posts to the thread/email | M | M-H | M | L-M | H | M-H | **Lighter cut of A** — the "pull" version of proactivity; fall back here if A is too big |
| **D** | **Agent health/observability surface**: operator-facing view over eval baselines + live traces — pass-rate trends, authority-miss/tool-choice rates, latency, silent-failure + threshold alerts | M-H | M | M | L | **VH** | M | **Solid, lower ceiling** — highest reuse of the eval/trace work, but it monitors the *agent*, not the *business*; less operator-differentiating |
| **E** | **Report generation/export** (Markdown/PDF) — old M1.5 | L-M | M | L-M | L | H | M | Minor; fold into C |
| **F** | New connectors / domain agents (procurement-planner, catalog-manager) | L | M | H | M | M | M | **Skip** — breadth, me-too, against R1/strategy |
| **G** | Prompt/model/tool versioning | L | M | M | L | M | L | **Skip for now** — invisible in a demo, no portfolio payoff yet |

## 5. Recommendation

**The product's single biggest weakness vs the 2026 category is that it is entirely reactive.** Closing
that is both the most impressive and the most strategically aligned move (it *deepens* the analyst into
an autonomous monitor rather than adding surface breadth).

Recommended path, sequenced:

1. **Slice 6 — Answer grounding + confidence (B), as the foundation.** Cheaper, very high leverage,
   and it retires R9 ("confidently wrong"). A proactive monitor that is confidently wrong is worse than
   none, so grounding should come first (or be built into A). Folds in the long-deferred groundedness
   eval as the measurement.
2. **Slice 7 — Proactive monitor + anomaly alerts (A), the marquee feature.** Build on grounded answers
   so every alert cites its evidence and authority. This is the demo headline: a console that lights up
   with *"Category X sales −18% w/w, likely driven by SKU-9 stockout — from `get_statistics`."*

If you want maximum impressiveness *first* and accept the de-risking debt, invert to A→B. If you want
the lightest possible proactive win, do **C** instead of **A**. **D** is the best pure-leverage option
if you'd rather showcase "we monitor and eval our own agent" than add a business-facing capability.

My pick: **B then A.** It sequences trust before autonomy, retires the top risk, and ends on the most
demoable feature the project could have.

## 6. Open questions (for the brainstorm on the chosen slice)

- **Background execution model:** how does a proactive monitor run without a logged-in operator turn?
  (A scheduler/worker calling the analyst with a system-actor identity; how does that intersect the new
  RBAC/actor model and the per-session runtime/sandbox + reaper?)
- **Alert noise control:** thresholds, dedupe, cool-down, and "is this actually anomalous" — the make-or-break
  UX problem for feature A.
- **Grounding granularity (B):** do we need structured/typed answer output to reliably link a number to
  its source rows, or can we attach citations at the tool-call level and reference them from the answer?
- **Confidence semantics:** authority-based (which tool) vs model-judged confidence vs both?
- **Delivery surface:** in-console alert center, a digest thread, email, or all three? What's demoable
  without standing up email infra?
- **Cost/cadence:** proactive monitoring repeatedly calls the model + backend — what cadence is
  defensible, and can a cheap deterministic pre-filter gate the expensive LLM cause-analysis?

## 7. Sources

- Shopify Sidekick (Winter '26 Pulse / proactive): https://www.getmesa.com/blog/shopify-sidekick/ ;
  https://what.digital/shopify-winter-edition-2026-new-features/ ;
  https://wearepresta.com/shopify-sidekick-features-2026-the-merchants-guide-to-agentic-commerce/
- Salesforce Agentforce observability / anomaly alerting:
  https://www.salesforce.com/news/stories/agentforce-studio-observability-tools-announcement/ ;
  https://www.salesforce.com/blog/agent-monitoring/ ; https://www.salesforce.com/blog/ai-agent-trends-2026/
- Conversational analytics (forecasting / anomaly / autonomous monitoring):
  https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/release-notes ;
  https://www.ampcome.com/post/ai-agents-in-analytics ; https://8allocate.com/blog/what-are-ai-agents-for-data-analysis/
- Trust / grounding / "show your work":
  https://www.thoughtspot.com/data-trends/artificial-intelligence/ai-generated-insights ;
  https://letsdatascience.com/news/microsoft-clarity-shows-grounding-queries-behind-citations-88f79032 ;
  https://www.yext.com/blog/7-data-backed-facts-on-ai-trust-and-consumer-decision-making-in-2026
