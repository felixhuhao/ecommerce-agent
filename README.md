# E-Commerce Agent

An AI operations console for ecommerce teams: it can answer business questions,
route work to focused specialists, generate interactive charts, monitor the
business for risks, and submit sensitive actions through human approval instead
of letting an agent mutate production state directly.

This repo owns the Python agent service, specialist orchestration, FastAPI API,
React operator console, trace/grounding pipeline, proactive monitors, and
sandboxed analytical execution. The operational Spring Boot MCP server lives in
the sibling repo `../ecommerce-mcp-server`.

## Why This Project Is Interesting

Most agent demos stop at "chat with tools." This project explores the harder
product surface around a real business agent:

- **Specialist routing instead of one giant agent.** Requests are routed to
  focused specialists such as sales analysis, inventory, purchasing,
  customer insights, order management, and optional data-warehouse analytics.
- **Tool surfaces are cataloged and role-shaped.** Specialists receive only the
  tools they need, selected through static metadata and tags rather than prompt
  guesswork.
- **Writes are approval-gated.** The model can propose purchase orders or order
  status changes, but approval and execution happen through a human-controlled
  REST path with actor/session binding.
- **Answers carry grounding.** Responses show whether they are authoritative,
  derived, or unverified, with source evidence from tool traces.
- **Charts are first-party artifacts.** The model emits a normalized chart spec;
  the frontend renders it with ECharts instead of relying on low-quality remote
  chart images.
- **Analytical code runs in a sandbox.** Forecast and data-analysis workflows can
  stage data files and execute Python in an isolated executor.
- **The system monitors proactively.** Low stock, sales drops, and stale orders
  appear as actionable alerts with evidence and acknowledgment flow.
- **MCP is a real extension boundary.** Spring business tools, optional warehouse
  NL2SQL tools, and legacy chart-tool experiments are integrated as external MCP
  services instead of being hard-wired into the agent.

## Architecture

```text
Operator Console (React + ECharts)
        |
        v
FastAPI service
  - auth, sessions, streaming, approvals, alerts
  - trace capture and answer grounding
  - specialist registry and runtime construction
        |
        +--> Router
        |      -> sales-analyst
        |      -> inventory
        |      -> purchasing
        |      -> order-manager
        |      -> customer-insights
        |      -> data-warehouse-analyst (optional)
        |
        +--> MCP clients
        |      -> Spring Boot ecommerce MCP server
        |      -> NL2SQL warehouse MCP server (optional)
        |
        +--> Sandbox executor
        |      -> staged files + Python analysis helpers
        |
        +--> MongoDB
               -> sessions, thread messages, traces, audits, alerts
```

The design deliberately separates **current operational state** from
**warehouse analytics**. Operational questions such as inventory, current orders,
suppliers, and approvals stay on Spring MCP tools. Historical/ad-hoc analytical
warehouse questions can route to the optional NL2SQL specialist.

## Key Workflows

### Read-Only Operations

Ask about current stock, order status, product sales, customer spend, or business
health. The router chooses the owning specialist, the specialist calls a narrow
tool or aggregate, and the answer is rendered with grounding sources.

Example:

```text
What's the current stock level for SKU-LOW-003?
```

### Interactive Analysis And Charts

For chartable answers, specialists call `create_chart_spec`, which returns a
validated chart artifact. The frontend renders it with ECharts, preserving axes,
legends, tooltips, and responsive layout.

Example:

```text
Show revenue by region and channel for the last 12 months as a chart.
```

### Sandboxed Forecasts

Forecast-style questions can stage product/order data into a sandbox workspace,
run Python analysis, and return both a grounded narrative and a chart artifact.

Example:

```text
Forecast next month's sales for SKU-LOW-003 and show the trend.
```

### Human-In-The-Loop Writes

Write-like requests become proposals, not direct mutations. The UI displays an
approval card. A human operator approves or rejects, and execution happens via
the backend approval API.

Example:

```text
Create a purchase order for 200 units of productId 9 from supplier 7.
```

### Proactive Monitoring

The alert center can run monitor checks and surface:

- low stock
- week-over-week sales drops
- stale pending orders
- paid orders that have not shipped

Each alert includes authority, sources, entities, and an acknowledgment action.

## Design Highlights

### Specialist Provider Catalog

Specialists are declared as providers with:

- name and routing description
- prompt key
- allowed tool tags
- role/RBAC enablement
- assembly function

This keeps specialist capabilities explicit and testable. Adding a tool to the
system is not enough; it must be tagged and assigned to the right specialist.

### Grounding And Trace Capture

Tool calls are captured into trace records. Grounding is built from those traces
and attached to thread messages and alerts. The UI can show source snippets and
confidence badges without asking the model to self-report where numbers came
from.

### First-Party Chart Artifacts

Instead of asking a chart MCP server to return a rendered image, the agent emits
a normalized chart spec:

- chart type
- axes
- series
- points
- notes

The frontend owns rendering quality through ECharts. This gives the demo a much
better visual surface while keeping MCP available for external-system extension
stories.

### Approval Boundary

Approval is intentionally not exposed as an agent tool. The model can request an
approval, but cannot approve its own request. Java owns durable approval records
and actor/session checks; FastAPI orchestrates the user-facing flow.

### Sandbox Boundary

Analytical code runs outside the main FastAPI process. The long-lived sandbox
executor keeps startup latency low while still isolating workspaces by session
and keeping analysis code away from application internals.

### Smoke And Regression Evals

The project includes focused tests for:

- routing
- tool choice
- approval safety
- grounding
- monitor checks
- chart artifacts
- live demo smoke paths

The goal is not only "does the code run" but "did the agent choose the intended
specialist and tool path."

## Repository Map

```text
src/ecommerce_agent/
  api/          FastAPI routes for sessions, auth, alerts, audit, health
  auth/         Login sessions, roles, and action permissions
  audit/        Audit projections over thread and approval activity
  specialists/ Specialist provider catalog and tool-surface assembly
  routing/     Router registry and classifier router
  tools/       First-party agent tools such as chart specs and analytics wrappers
  trace/       Trace capture, projection, persistence
  threads/     Mongo-backed conversation message storage
  grounding/   Answer grounding and source extraction
  monitoring/  Proactive alert checks and alert grounding
  sandbox/     Docker/HTTP sandbox execution backends
  sessions/    Runtime construction, turn execution, streaming events
  evals/       Routing/tool/grounding/live smoke eval harnesses

frontend/
  React operator console, ECharts renderer, alert center, trace UI

docs/
  Design records and development notes
```

## Related Repositories

- `../ecommerce-mcp-server`
  ([felixhuhao/ecommerce-mcp-server](https://github.com/felixhuhao/ecommerce-mcp-server))
  - Spring Boot MCP server for operational ecommerce tools and approval execution.
- `../nl2sql_pro`
  ([felixhuhao/nl2sql-data-agent](https://github.com/felixhuhao/nl2sql-data-agent))
  - optional NL2SQL MCP service for warehouse-style analytics.

## Development

Setup, configuration, service tokens, Docker commands, and test commands live in
[docs/development.md](docs/development.md).
