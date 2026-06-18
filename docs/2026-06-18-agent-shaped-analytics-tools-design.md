# Agent-Shaped Analytics Tools Design

## 1. Goal

Reduce repeated low-level tool calls and demo surprises by splitting the most
important `get_statistics` aggregates into small, agent-shaped analytical tools.

The current system has good specialist boundaries, but some analytical questions
still force the model to assemble business aggregates from low-level reads. That
creates loops such as many `order_query`, `user_query`, or `query_readonly` calls,
then pushes the fix pressure into prompts and runtime caps. Caps are necessary,
but they should be the backstop, not the main workflow.

This slice defines the next tool-contract improvement: common ecommerce analytics
should be exposed as bounded, named tools with structured inputs and outputs,
rather than as one broad grab-bag aggregate.

## 2. Non-Goals

- No keyword-matching router rules.
- No broad prompt rewrite.
- No replacement of the specialist-provider architecture.
- No replacement of ECharts.
- No raw SQL UI.
- No attempt to make every possible `get_statistics` key a first-class tool.
- No removal of existing low-level tools; they remain fallback and drill-down
  tools.
- No Java MCP endpoint work in v1 unless the wrapper spike proves the existing
  `get_statistics` payload is insufficient.

## 3. Background

Recent manual and live-smoke testing exposed the same pattern in several places:

- "Which customer groups are spending the most?" initially routed poorly, then
  fanned out through customer and order reads.
- Warehouse questions could repeat schema/query calls when a single analytical
  query should have been enough.
- Chart requests worked better after `create_chart_spec`, but the model still
  needs high-quality data before charting.

The fixes so far are useful:

- specialist routing boundaries;
- first-party `create_chart_spec`;
- per-tool call budgets;
- smoke assertions for route, tools, max calls, artifact, and grounding.

But prompt guidance and call budgets do not change the fact that the model is
being asked to pick between low-level APIs and a broad aggregate tool whose
useful keys are hidden inside one large response. Real tool-using agents work
better when tools are shaped around the task the agent is trying to complete, not
around thin CRUD endpoints or underspecified aggregate bags.

## 4. Why `get_statistics` Is Not Enough

`get_statistics` already exposes at least two aggregates this design cares about:

- `topCustomersBySpend`
- `salesByCategory`

The smoke design currently pins those keys, and live smoke already requires
`get_statistics` for customer top-spend and sales-category chart cases.

So the problem is not that an aggregate is absent. The problem is discoverability
and trust:

- `get_statistics` is a broad tool name. It does not tell the model which
  business question family it answers.
- The model must know, from prompt text, that `topCustomersBySpend` or
  `salesByCategory` exists inside the response.
- The response shape is a grab bag. The model can still decide it needs
  `user_query` or `order_query` to "verify" or "recompute" an aggregate that the
  backend already owns.
- Live failures showed that even after prompt nudges, the model sometimes fans
  out into low-level calls.

The v1 tools below are therefore wrappers over existing Spring aggregate data,
not new sources of truth. Their job is to make the contract explicit:

```text
customer spend ranking question -> customer_spend_summary
sales by category question      -> sales_by_category
```

`get_statistics` remains the broad fallback for miscellaneous aggregate keys and
existing checks. The shaped tools become the required path for their specific
question families.

## 5. Decision

Add a small, curated layer of analytical wrapper tools.

These tools should:

- answer one recognizable business question family;
- have a narrow input schema;
- return compact structured rows plus a short summary;
- include enough provenance for grounding;
- avoid forcing the model to loop over records;
- be owned by one source of truth;
- be covered by live smoke tests for required/forbidden tools and max call count.

Keep low-level reads available for drill-down. The target path is:

```text
operator asks common aggregate
  -> router selects the owning specialist
  -> specialist calls one aggregate tool
  -> optional create_chart_spec
  -> answer with authoritative/derived grounding
```

The fallback path remains:

```text
operator asks uncommon or custom analysis
  -> specialist uses low-level reads, sandbox, or NL2SQL
  -> runtime caps prevent runaway exploration
```

## 6. Source Ownership

### 6.1 Spring MCP

Spring remains the canonical operational source for current ecommerce facts and
trusted business aggregates.

V1 implementation choice: add first-party Python wrapper tools that call the
existing Spring `get_statistics` tool internally and extract one named aggregate.

This is option (c) from the implementation choices, with Python ownership:

- not new Java MCP endpoints yet;
- not wrappers around low-level row reads;
- not a second source of truth;
- no internal Spring read events in the trace except the wrapper's own tool call.

The wrapper tool name is what grounding and smoke should assert. The internal
`get_statistics` call is an implementation detail.

Good future Spring aggregate candidates:

| Tool | Owner | Purpose |
|---|---|---|
| `customer_spend_summary` | customer-insights | top customers by spend, backed by `get_statistics.topCustomersBySpend` |
| `sales_by_category` | sales-analyst | category totals/share, backed by `get_statistics.salesByCategory` |
| `product_sales_summary` | sales-analyst | top products, units, revenue, margin-like fields if available |
| `inventory_reorder_summary` | inventory | low-stock list with SKU/name/safety stock/shortage |

Spring aggregate tools should be preferred when the user asks for current
operational answers or demo-stable business summaries.

### 6.2 NL2SQL MCP

NL2SQL remains the optional warehouse/ad-hoc analytical connector.

Good NL2SQL aggregate candidates:

| Tool | Owner | Purpose |
|---|---|---|
| `warehouse_metric_query` | data-warehouse-analyst | run a governed semantic metric by dimensions/filters |
| `cohort_retention_summary` | data-warehouse-analyst | cohort and retention analysis |
| `region_channel_summary` | data-warehouse-analyst | historical region/channel breakdowns |

The first NL2SQL improvement should not be "let the model write more SQL." It
should be a metric-shaped or analysis-shaped tool that calls NL2SQL's guarded
query path internally and returns bounded results.

### 6.3 Sandbox

The sandbox remains for Python analysis over staged files, mostly for forecasts,
time series, and computations that are not worth making into canonical backend
tools yet.

Do not use sandbox as the default answer path for common aggregates such as top
customers, sales by category, or low-stock lists.

## 7. Tool Contract

### 7.1 Input Schemas

V1 intentionally keeps input schemas small.

`customer_spend_summary`:

```json
{
  "limit": 10
}
```

Rules:

- `limit` is optional and defaults to 10.
- `limit` is clamped to a small maximum, for example 20.
- No mode enum in v1. The tool answers top customers by spend only.
- Spend bands, repeat-vs-one-time spend, and customer cohorts are deferred.

`sales_by_category`:

```json
{
  "limit": 10,
  "include_unknown": true
}
```

Rules:

- `limit` is optional and defaults to all returned categories up to the cap.
- `include_unknown` defaults to true so category-mapping gaps stay visible.
- No time-window parameter unless the current `get_statistics` payload already
  supports it without low-level recomputation.

### 7.2 Output Shape

Each new aggregate tool should return a dict shaped like:

```json
{
  "kind": "analytics_result",
  "metric": "customer_spend_summary",
  "source": "spring",
  "filters": {"limit": 10},
  "rows": [
    {"customerName": "Acme Co", "orders": 12, "revenue": 12345.67}
  ],
  "summary": {
    "row_count": 1,
    "currency": "USD",
    "generated_at": "2026-06-18T00:00:00Z"
  },
  "provenance": {
    "system": "spring-mcp",
    "tables": ["orders", "users"],
    "query_id": "optional"
  }
}
```

Rules:

- `rows` must be capped.
- Large raw records must not be returned.
- Include stable keys suitable for charting.
- Include readable labels such as `sku`, `productName`, `customerName`, or
  `category` when available.
- Include source/provenance so grounding can stay Authoritative for canonical
  Spring aggregates and Derived for sandbox/warehouse synthesis when appropriate.
- Tool descriptions should state when the result is already sufficient and when
  low-level drill-down is appropriate.

The response shape does not need to be identical for every tool, but the same
top-level concepts should stay recognizable: metric, filters, rows, summary, and
provenance.

Grounding caveat: runtime authority is not inferred from `provenance.system`.
`src/ecommerce_agent/grounding/build.py` uses static tool-name allowlists. The new
tool names must be added to the authoritative read-tool set. Provenance is useful
operator-facing evidence, not the authority switch.

## 8. Initial Scope

Start with two high-impact tools:

### 8.1 `customer_spend_summary`

Owner: first-party Python wrapper over Spring aggregate data, selected by
`customer-insights`.

Supports:

- top customers by spend;
- optional top-N limit;

Why first:

- It directly addresses the repeated customer/order fanout observed in live
  testing.
- It supports a strong ECharts demo with bar charts.
- It belongs clearly to `customer-insights`, not `sales-analyst` or NL2SQL.

Deferred:

- spend bands;
- repeat-vs-one-time spend;
- cohort or retention analysis.

Those are separate question families and should not be hidden behind a
parameter-heavy v1 interface.

### 8.2 `sales_by_category`

Owner: first-party Python wrapper over Spring aggregate data, selected by
`sales-analyst`.

Supports:

- category revenue;
- share of total when available;
- units sold or date window only if the current `get_statistics` payload already
  exposes them without low-level recomputation.

Why second:

- It is a common demo prompt.
- It avoids sandbox or NL2SQL for a simple canonical aggregate.
- It gives a clean column/bar ECharts path.

## 9. Python Integration

For each new tool:

1. Add first-party Python wrapper tools, likely near
   `src/ecommerce_agent/tools/`.
2. Wire the wrappers in the specialist assembly seam, similar to
   `create_chart_spec` and `stage_sales_analysis_inputs`.
3. Add exact tool metadata in `src/ecommerce_agent/tools/metadata.py`.
4. Assign a narrow tag, for example:
   - `customers.aggregate`
   - `analytics.category`
5. Note that `analytics.aggregate` is already used by `get_statistics`; keep the
   new tags separate so ownership is explicit.
6. Add the tag only to the owning specialist in
   `src/ecommerce_agent/specialists/providers.py`.
7. Keep low-level tags available, but update prompts to prefer the aggregate
   tool when it directly answers the question.
8. Add live labels so the status tracker says what is happening without exposing
   implementation details.
9. Add the tool names to the authoritative grounding allowlist and the
   data-bearing metadata.
10. Update the smoke design and live smoke cases that currently require
   `get_statistics` for `customer_top_spend` and `sales_category_chart`.

Avoid giving the aggregate tools to every specialist. Tool surface size is part
of the control plane.

## 10. Test Plan

### 10.1 Deterministic Tests

- Tool metadata selects the new tool only for the owning specialist.
- Provider tests prove other specialists do not receive it.
- Prompt tests pin the general boundary without keyword special-casing.
- Unit tests prove the wrapper extracts the intended key from a stub
  `get_statistics` result and caps rows.
- Contract smoke verifies `get_statistics` still exposes the backing keys until
  Java-native endpoints replace the wrappers.

### 10.2 Live Smoke

Add or tighten cases:

| Case | Expected route | Required tools | Forbidden tools | Budgeted tools |
|---|---|---|---|---|
| customer groups by spend | customer-insights | `customer_spend_summary` | writes, sandbox tools, NL2SQL tools | `user_query <= 2`, `order_query <= 2` |
| top customers chart | customer-insights | `customer_spend_summary`, `create_chart_spec` | writes, sandbox tools, NL2SQL tools | `user_query <= 2`, `order_query <= 2` |
| sales by category chart | sales-analyst | `sales_by_category`, `create_chart_spec` | writes, sandbox tools, NL2SQL tools | `get_statistics <= 1` |

Each case should assert:

- completion before timeout;
- expected specialist;
- required tool presence;
- forbidden tool absence;
- max call count per tool;
- grounding authority;
- ECharts artifact when requested.

Low-level tools are not categorically forbidden if they are valid drill-down
tools. They are budgeted. Forbidden means "wrong surface for this prompt," such
as writes, sandbox tools for direct aggregates, or NL2SQL for current
operational aggregates.

## 11. Prompt Policy

Prompts may describe the tool's purpose, but should not encode demo-specific
question strings.

Acceptable:

- "For top customer spend rankings, use `customer_spend_summary`."
- "Use low-level `order_query` only for a specific customer's order history."

Avoid:

- "If the user asks 'Which customer segments or groups are spending the most',
  call tool X."
- lists of exact demo prompts;
- regex-like routing instructions;
- instructions that require the model to infer hidden IDs or naming conventions.

Runtime tool caps stay in place. They are a safety rail, not the primary UX.

## 12. Cross-Doc Updates

The smoke design currently names `get_statistics` as the required path for:

- `customer_top_spend`
- `sales_category_chart`

When the shaped tools land, update
`docs/2026-06-15-m4-slice12-smoke-coverage-design.md` and
`tests/integration/test_demo_live_smoke.py` so:

- `customer_top_spend` requires `customer_spend_summary`;
- `sales_category_chart` requires `sales_by_category`;
- `get_statistics` remains required only for backing-key contract smoke and
  miscellaneous aggregate fallback cases.

## 13. Risks

| Risk | Mitigation |
|---|---|
| Too many aggregate tools | Start with two; add only after smoke/manual evidence |
| Aggregate shape drifts | Contract smoke pins required keys and row caps |
| Tool name collision | Keep exact metadata entries and source notes |
| Backend work grows | Prefer tools that wrap existing aggregate queries first |
| Prompt special-casing returns | Prompt tests should reject exact demo-question recipes |
| NL2SQL vs Spring inconsistency | Keep source ownership explicit; do not silently blend |
| Parallel paths confuse the model | Shaped tools are required for their families; `get_statistics` is fallback |

## 14. Acceptance

- `customer_spend_summary` is the required path for top-customer spend prompts;
  `get_statistics.topCustomersBySpend` is its backing data source, not the
  direct model-facing path.
- `sales_by_category` is the required path for sales-by-category prompts;
  `get_statistics.salesByCategory` is its backing data source, not the direct
  model-facing path.
- At least one customer spend prompt completes with one aggregate tool call and
  no per-customer order fanout.
- At least one sales-by-category chart prompt completes with one aggregate tool
  call and one `create_chart_spec` call.
- Existing low-level drill-down prompts still work.
- Runtime caps remain active.
- Smoke tests cover route, tool choice, max calls, grounding, and artifacts.
- No new keyword-matching router or prompt rules are introduced.
