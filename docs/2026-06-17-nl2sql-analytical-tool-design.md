# NL2SQL Analytical MCP Design

## 1. Goal

Add `felixhuhao/nl2sql-data-agent` as an optional MCP-backed analytical connector for
warehouse-style questions.

This gives the demo a stronger MCP extension story than chart rendering:

- Spring MCP remains the operational ecommerce system.
- First-party ECharts remains the chart-rendering path.
- NL2SQL MCP becomes an external analytical data source with governed, read-only SQL.

The integration should reduce surprises, not add another ambiguous tool pile. The core design
decision is to add a separate `data-warehouse-analyst` specialist instead of giving NL2SQL tools to
the existing `sales-analyst`.

## 2. Non-Goals

- No write access through NL2SQL.
- No replacement of the Spring MCP operational tools.
- No automatic reconciliation when Spring and warehouse data disagree.
- No prompt keyword-matching rules as the primary control plane.
- No raw SQL editor in the ecommerce-agent UI.
- No required NL2SQL service in the default local stack or default CI.
- No new chart renderer. Warehouse charts use `create_chart_spec` and ECharts.

## 3. Background

The ecommerce agent now has five operational specialists:

- `sales-analyst`
- `order-manager`
- `purchasing`
- `inventory`
- `customer-insights`

Those specialists are backed by Spring MCP tools over the operational ecommerce schema. The NL2SQL
project is different: it is an OLAP-focused data agent with semantic metadata, guarded read-only SQL,
DuckDB/ClickHouse datasources, chart recommendations, smoke evals, and MCP tools such as schema
inspection, read-only query, explain, and metric search.

That difference is useful only if the product draws a clear boundary. If both Spring and NL2SQL are
quietly available to the same specialist for the same question, the model can produce inconsistent
answers and the operator cannot tell which source of truth was used.

## 4. Decision

Introduce a new read-only specialist:

```text
data-warehouse-analyst
  -> NL2SQL MCP schema/query/explain/metric tools
  -> create_chart_spec
  -> no Spring operational tools
  -> no sandbox backend
  -> no approval tools
```

Do not add NL2SQL tools to `sales-analyst` in this slice.

Why:

- Source ownership is explicit.
- Tool-choice evals can assert that warehouse questions use NL2SQL and operational questions do not.
- The warehouse specialist can use NL2SQL's own guardrails instead of bypassing them with sandbox code.
- Existing demo prompts keep their current Spring-backed behavior unless the user asks for warehouse
  analytics.
- ECharts still gives high-quality charts without relying on an external chart MCP.

## 5. Source Boundary

### 5.1 Spring MCP: Operational Source of Truth

Use existing specialists and Spring MCP for current operational state:

- Current inventory and low-stock checks.
- Product identity and active catalog lookup.
- Order status and order updates.
- Supplier and purchase-order state.
- Approval proposal creation and execution.
- Current operational customer/order/product reads.

Examples:

- "Is SKU-LOW-003 below safety stock?"
- "Create a PO for productId 9 from supplier 7."
- "What is the status of order 1007?"
- "Who supplies productId 9?"

### 5.2 NL2SQL: Analytical Warehouse Source

Use `data-warehouse-analyst` for historical, ad-hoc, warehouse-style analytics:

- Long-range trends.
- Cohorts and retention.
- Custom joins not represented by Spring aggregate tools.
- Region/channel/category breakdowns over OLAP tables.
- Moving averages, YoY/MoM, share-of-total, Top-N over warehouse facts.
- Questions that explicitly ask for warehouse data, SQL-backed analysis, or analytical tables.

Examples:

- "Compare monthly revenue by region for the last 12 months."
- "Show repeat purchase rate by customer cohort."
- "Which channel had the highest average order value last quarter?"
- "Break down the last 90 days by region, then chart order count and revenue."

### 5.3 Ambiguity Rule

If a prompt can be answered cleanly by an existing operational specialist, keep it there.

This is intentionally conservative. The warehouse specialist is for broader analytical scope, not a
new default for every sales question. For example:

- "Top customers by spend" stays `customer-insights` unless the user asks for warehouse, cohort,
  region/channel breakdown, or long historical analysis.
- "Compare sales by category" stays `sales-analyst` if Spring aggregate data is enough.
- "Current stock" never goes to NL2SQL.

If the user asks to compare operational and warehouse answers, the agent should state that it is
comparing two sources and include a freshness caveat. Silent blending is out of scope.

## 6. MCP Tool Contract

The implementation should first run a discovery spike against the NL2SQL MCP server and record the
actual tool names. The README-level contract suggests this shape:

| Tool class | Expected purpose | ecommerce-agent tag |
|---|---|---|
| Schema/list tools | Discover tables, columns, metrics, and analysis spaces | `warehouse.schema` |
| Guarded query tool | Execute SELECT-only, scoped SQL through NL2SQL guardrails | `warehouse.query` |
| Explain tool | Explain query plan or performance characteristics | `warehouse.explain` |
| Metric search tool | Retrieve semantic metric definitions / verified query hints | `warehouse.metric` |

Do not depend on exact names in the design. Phase 0 records them and the code allowlists the observed
names.

Important implementation consequence: tool selection currently runs through the static
`TOOL_META` tuple in `src/ecommerce_agent/tools/metadata.py`. Phase 0's output must therefore be
baked into `TOOL_META` as exact `ToolMeta` entries with `warehouse.*` tags. Runtime discovery can
verify those names still exist, but it does not replace static metadata. If the NL2SQL server renames
a tool, selection will fail until the metadata allowlist is updated.

Required properties:

- Tools are read-only from the ecommerce-agent perspective.
- Query execution stays inside the NL2SQL service guard path.
- Results are capped before entering the LLM context.
- Tool output includes enough structured detail for grounding:
  - SQL or query id.
  - datasource name.
  - table/metric names when available.
  - row preview or summarized result.
  - guard/explain diagnostics when available.

## 7. Specialist Runtime

Add an optional provider:

```python
SpecialistProvider(
    name="data-warehouse-analyst",
    description=(
        "read-only warehouse analytics: ad-hoc SQL-backed historical analysis, "
        "cohorts, retention, region/channel breakdowns, long-range trends, "
        "and metric exploration. Not for current operational state or writes."
    ),
    capability="read",
    prompt_key="data_warehouse_analyst",
    tool_tags=frozenset({"warehouse.schema", "warehouse.query", "warehouse.explain",
                         "warehouse.metric", "viz.chart"}),
    assemble=_assemble_data_warehouse_analyst,
)
```

Runtime rules:

- `backend=None`.
- No Spring tools.
- No approval tools.
- Include `create_chart_spec` for chart requests.
- Include only NL2SQL tools from an explicit allowlist.
- If NL2SQL is not configured, do not register this provider in the router registry.

The last rule is important. If the router can select `data-warehouse-analyst` while the runtime cannot
build it, normal user prompts become avoidable policy/unavailable errors.

This needs a settings-aware provider mechanism; the current provider tuple is static:

- `src/ecommerce_agent/specialists/providers.py` exposes module-level `PROVIDERS`.
- `src/ecommerce_agent/routing/registry.py:build_specialist_registry()` imports that tuple with no
  `Settings` argument.
- `SpecialistProvider.is_enabled(actor)` only gates by actor role, not connector availability.

Do not overload `is_enabled(actor)` for NL2SQL. Add a separate capability filter, for example:

```python
def routeable_providers(settings: Settings | None = None) -> tuple[SpecialistProvider, ...]:
    ...
```

Then thread it through both paths:

- `build_session_runtime(...)` uses `routeable_providers(settings)` before applying
  `provider.is_enabled(actor)`.
- `build_specialist_registry(settings=None, providers=None)` can build the default static registry for
  old tests, or an enabled registry for NL2SQL evals/runtime.

The registry and factory must use the same provider list for connector availability. Role shaping
still happens after that list is chosen.

The provider sketch also implies new build wiring. Today `SpecialistProvider.build()` receives only
`spring_tools`, `viz_tools`, and `backend`, and `build_session_runtime()` loads only
`SPRING_SERVER_NAME`. NL2SQL needs:

- an NL2SQL MCP server name and connection in `mcp_client.py`;
- an optional `warehouse_tools` pool loaded in `sessions/factory.py`;
- a `warehouse_tools` parameter threaded through `SpecialistProvider.build()`;
- `_assemble_data_warehouse_analyst(...)` in `specialists/providers.py`;
- `build_data_warehouse_analyst(...)` in `agents.py`;
- tests proving existing specialists receive an empty/no-op warehouse pool.

## 8. Configuration

Add settings:

```text
nl2sql_mcp_url = ""
nl2sql_mcp_service_token = ""
nl2sql_enabled = false
```

`nl2sql_enabled` must be true and `nl2sql_mcp_url` must be non-empty for the provider to be registered.

This keeps the default demo stack stable. The NL2SQL service can run as a separate project/stack and
be attached only when the connector is being demonstrated.

## 9. Routing Design

The router gets a new provider description from the provider registry. Avoid a long keyword list in the
router prompt. The useful distinction is source and task shape:

- Operational/current/action/proposal -> existing Spring-backed specialists.
- Historical/ad-hoc/warehouse/semantic metric/custom SQL analysis -> `data-warehouse-analyst`.

Routing evals should carry the specificity, not increasingly elaborate prompt prose.

Because `data-warehouse-analyst` is optional, routing evals need two registry modes:

- default registry: NL2SQL disabled, no warehouse route present;
- enabled registry: NL2SQL enabled, warehouse route present.

The required warehouse eval cases below run only against the enabled registry. The operational
regression cases should run in both modes when cheap, so enabling NL2SQL cannot steal current-state
traffic.

Required routing eval cases:

| Prompt | Expected route |
|---|---|
| "is SKU-LOW-003 below safety stock?" | `inventory` |
| "create a PO for productId 9 from supplier 7" | `purchasing` |
| "top customers by spend" | `customer-insights` |
| "compare sales by category" | `sales-analyst` |
| "show repeat purchase rate by cohort over the last 12 months" | `data-warehouse-analyst` |
| "break down last 90 days revenue by region and channel" | `data-warehouse-analyst` |
| "use warehouse data to compare monthly revenue YoY" | `data-warehouse-analyst` |

Add at least one adversarial case:

- "current stock from the data warehouse for SKU-LOW-003" should either route to `inventory` or produce
  a source-boundary clarification. It must not silently answer current stock from stale warehouse data.

## 10. Grounding and Sources

Warehouse answers should be grounded from NL2SQL tool outputs.

Authority rule:

- If a guarded NL2SQL query succeeds and the answer is clearly warehouse-domain analytics, mark the
  answer `Authoritative` for that analytical source.
- Include a source note such as "Warehouse analytics source; may lag operational systems."
- If the answer mixes warehouse and Spring sources in a future slice, downgrade to `Derived` unless the
  response explicitly compares sources and explains the freshness boundary.
- If schema search succeeds but query execution fails, do not mark authoritative.

This keeps the existing confidence badge useful without pretending that warehouse data is the same as
operational current state.

Implementation maps to `src/ecommerce_agent/grounding/build.py`. Today `build_grounding()` marks an
answer authoritative when `AUTHORITATIVE_READ_TOOLS` intersects the fired tools; otherwise sandbox
evidence becomes `Derived` and numeric unsupported claims become `Unverified`. Warehouse query tools
that represent successful governed analytical reads must be added to that authoritative path, or the
doc's authority rule will not be visible in the UI. Schema/metric lookup alone should stay
non-authoritative unless a governed query also succeeds.

Concretely: add **only** the governed `warehouse.query` tool to `AUTHORITATIVE_READ_TOOLS`, not the
schema/metric tools. Those tools are still tagged `data_bearing=True` (§11), so `_sources()` will list
them as grounding sources, but authority is driven solely by `AUTHORITATIVE_READ_TOOLS` membership.
Adding schema/metric to that set would let a metadata-only lookup mark an answer authoritative, breaking
the "schema alone is not authoritative" rule.

## 11. Trace and Progress

Add tool metadata for NL2SQL tools:

- `data_bearing=True` for guarded query results and metric/schema tools that return evidence.
- Live labels:
  - schema/metric tools: "Reading warehouse metadata"
  - query tool: "Querying warehouse"
  - explain tool: "Explaining warehouse query"

Trace should show the actual NL2SQL MCP calls. The operator should be able to see whether an answer
came from Spring operational tools or the warehouse connector.

## 12. Health and Diagnostics

Extend health diagnostics only when NL2SQL is enabled:

```json
{
  "nl2sql": {
    "configured": true,
    "status": "ok",
    "tool_count": 4,
    "allowed_tools": ["..."],
    "missing_expected_tools": []
  }
}
```

If not enabled:

```json
{"nl2sql": {"configured": false}}
```

Do not fail general app health when NL2SQL is disabled.

Implementation target: `src/ecommerce_agent/api/app.py:/health/mcp` and the helpers in
`src/ecommerce_agent/mcp_client.py` that determine configured MCP servers and probe each server.
The general `/health` component summary can remain config-only unless a later slice needs a dedicated
NL2SQL component.

## 13. Testing

### 13.1 Unit Tests

- Provider is absent when NL2SQL is disabled.
- Provider is present when NL2SQL is enabled and configured.
- Provider selects only `warehouse.*` tools plus `create_chart_spec`.
- Provider excludes Spring, approval, sandbox, and filesystem tools.
- Router registry includes `data-warehouse-analyst` only when configured.
- Default routing eval registry stays unchanged when NL2SQL is disabled.
- Enabled routing eval registry includes `data-warehouse-analyst`.
- `SpecialistProvider.build()` can receive a warehouse tool pool without changing existing provider
  tool surfaces.
- Tool metadata marks NL2SQL query evidence as data-bearing.
- Grounding builds sources from NL2SQL query output.
- Health reports configured/unconfigured NL2SQL state.

### 13.2 Deterministic Tool-Choice Tests

Use stub NL2SQL tools:

- Warehouse cohort prompt calls `warehouse.query`.
- Warehouse chart prompt calls `warehouse.query` then `create_chart_spec`.
- Current stock prompt does not call any NL2SQL tool.
- PO/action prompt does not call any NL2SQL tool.
- Customer top-spend hero prompt stays on existing customer path unless explicitly warehouse-scoped.

### 13.3 Optional Live Smoke

Run only when `RUN_NL2SQL_LIVE=1` and NL2SQL config is present:

- Discover MCP tools and assert the expected allowlist exists.
- Ask one warehouse analytical question and assert:
  - route is `data-warehouse-analyst`
  - guarded query tool fired
  - answer is grounded
  - no Spring write/approval/sandbox tools fired
- Ask one operational current-state question and assert route is not `data-warehouse-analyst`.

Keep this out of required CI until both repos have a stable shared test harness.

## 14. Phasing

### Phase 0 - Discovery Spike

- Run NL2SQL MCP locally.
- Record exact MCP tool names, input schemas, and result shapes.
- Decide the concrete allowlist.
- Capture one representative tool trace.

Exit criteria: the implementation plan can name exact tools instead of placeholders.

### Phase A - Optional Connector Skeleton

- Add config.
- Add MCP loading path for NL2SQL.
- Add tool metadata tags.
- Add health diagnostics.
- No routeable specialist yet.

Exit criteria: app starts with NL2SQL disabled, and health can inspect it when enabled.

### Phase B - Data Warehouse Specialist

- Add provider, prompt, registry gating, and runtime assembly.
- Add deterministic routing/tool-choice tests.
- Add grounding and trace labels.

Exit criteria: warehouse prompts route correctly and operational prompts do not regress.

### Phase C - Live Smoke and Demo Questions

- Add optional live smoke.
- Add a short demo question set.
- Document startup with the external NL2SQL stack.

Exit criteria: manual demo can show Spring operational tools, NL2SQL analytics, and ECharts charts as
three distinct extension surfaces.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Router confusion between sales/customer/warehouse analytics | Separate specialist, source boundary evals, conservative ambiguity rule |
| Data inconsistency between operational DB and warehouse | Source note and no silent blending |
| NL2SQL tool names drift | Discovery phase bakes exact names into `TOOL_META`; health/live smoke verifies them |
| Large SQL results bloat thread messages | Row/result caps before LLM context |
| SQL safety depends on external service | Use only guarded read-only NL2SQL MCP tools |
| Optional service breaks default demo | Provider registered only when enabled/configured |
| Prompt over-special-casing creeps back | Keep routing rules source-based; put coverage in eval cases |

## 16. Acceptance Criteria

- Default ecommerce-agent startup works with NL2SQL disabled.
- Enabling NL2SQL registers exactly one new read-only provider: `data-warehouse-analyst`.
- The provider has no Spring, approval, sandbox, filesystem, or direct write tools.
- Warehouse-domain prompts route to `data-warehouse-analyst`.
- Current operational prompts keep routing to existing specialists.
- Warehouse answers show trace/sources from NL2SQL tools.
- Warehouse chart answers use `create_chart_spec`, not external chart MCP.
- Optional live smoke proves the connector without making cross-repo CI required.
