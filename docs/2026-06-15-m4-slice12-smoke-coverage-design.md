# M4 Slice 12 — Demo Smoke Coverage Design

## 1. Goal

Add a small smoke suite that catches demo-breaking regressions before manual UI testing.

The suite should cover the recent expanded surface:

- Five-specialist routing and role-shaped tool access.
- Java MCP contract drift after dataset/tool changes.
- Live LLM tool choice loops and repeated read-tool fanout.
- Grounding authority badges for backend reads, sandbox analysis, and unsupported claims.
- Chart artifact creation and basic chart-tool choice.
- Approval proposal creation and status wiring.

This is a tripwire, not a replacement for the deeper routing/tool-choice/groundedness evals.

## 2. Non-Goals

- No exhaustive eval benchmark.
- No pixel-perfect visual testing.
- No Docker sandbox architecture changes.
- No prompt rewrite unless a smoke exposes a clear defect.
- No new dependency download at test-time. Tier 0 still requires Spring MCP (and the
  chart MCP for the chart-surface check) to be running locally; if either is down,
  the affected checks skip cleanly so the rest of Tier 0 still runs.

## 3. Test Tiers

### 3.1 Tier 0 — Deterministic Contract Smoke

Runs by default. No live LLM.

Purpose: prove local services and static contracts are shaped for the demo before we spend LLM
tokens.

Checks:

- Spring MCP is reachable.
- `get_statistics` exposes aggregate keys used by the demo. These names currently
  appear only in prompts, not in any captured response in this repo, so confirm
  them against the live Spring MCP before locking the assertion:
  - `salesByCategory`
  - `topCustomersBySpend`
  - existing sales/time aggregates used by forecast prompts
- `inventory_low_stock` returns human-readable rows. The demo's grounding confidence
  depends on readable evidence (`sku`, `productName`), so these are REQUIRED once
  confirmed — not soft forever. The only captured sample in this repo
  (`tests/test_monitoring_checks.py`) shows `productId`, `quantity`, `safetyStock`;
  `sku`, `productName`, and `shortage` are accepted as aliases by
  `src/ecommerce_agent/monitoring/checks.py` but are not yet confirmed in any captured
  response. Action before merging Tier 0: run the live Spring MCP once, capture the
  actual row, and lock the full set as required. Target required shape:
  - `productId` (required now — confirmed in captured sample)
  - `quantity` (required now — confirmed in captured sample)
  - `safetyStock` (required now — confirmed in captured sample)
  - `sku` (required after live confirmation)
  - `productName` (required after live confirmation)
  - `shortage` (required after live confirmation, if the backend computes it)
- `product_search("SKU-LOW-003")` resolves to the expected product. Confirm
  `SKU-LOW-003` exists in the seed data before relying on it; the only LOW SKU
  fixture in the repo is in `src/ecommerce_agent/evals/approval_safety.py`, not
  in a product seed.
- ModelScope chart MCP exposes the expanded chart surface from `VIZ_TOOL_NAMES`.
  This check requires the chart MCP to be running (see §2); skip cleanly if it is
  not reachable so the Spring-only checks still run locally.
- Specialist providers expose expected tool sets, pinned to the exact names locked
  by `tests/test_specialists.py`:
  - inventory: `product_search`, `inventory_query`, `inventory_low_stock`
  - purchasing: `product_search`, `supplier_query`, `supplier_top`,
    `purchase_order_query`, `request_approval`
  - customer-insights: `user_query`, `order_query`, `get_statistics` (no write
    tools, no `request_approval`)

Suggested location:

```text
tests/integration/test_demo_contract_smoke.py
```

This test may skip if Spring MCP or chart MCP is not running, but it must not call the LLM.

### 3.2 Tier 1 — Live API Prompt Smoke

Runs only with `RUN_LIVE_LLM=1`.

Purpose: exercise the real FastAPI session path with the configured model, real MCP tools, trace
capture, grounding, and thread persistence.

Use the API, not direct agent calls, so the smoke covers the same path the UI uses:

```text
POST /api/sessions
POST /api/sessions/{id}/messages
GET /api/sessions/{id}
GET /api/sessions/{id}/turns/{turn_id}/trace
```

All four routes take `ActorDep` → `current_actor` (`src/ecommerce_agent/api/sessions.py`,
`src/ecommerce_agent/auth/dependencies.py`), which requires a valid auth cookie backed by
the login-session store. The smoke MUST establish an authenticated actor before any call —
an unauthenticated `POST /api/sessions` will 401. Two acceptable approaches:

1. **Dependency override (default, matches existing tests).** Override `current_actor`
   with an `Actor` — NOT a `RuntimeActor`. `current_actor` is typed to return `Actor`
   (`src/ecommerce_agent/auth/dependencies.py:23`), which has no `can_propose` field;
   `can_propose` is derived later from `actor.role` via `can(role, Action.PROPOSE)`
   (`src/ecommerce_agent/api/sessions.py:230,326`). `Role.OPERATOR` maps to
   `can_propose=True` (`src/ecommerce_agent/auth/permissions.py:8-9`). Use:

   ```python
   app.dependency_overrides[current_actor] = lambda: Actor(
       user_id="op1", username="op1", role=Role.OPERATOR, spring_user_id=<op1 spring id>
   )
   ```

   (see `tests/test_sessions_api.py:37,152` for the exact `Actor.from_user(...)` pattern).
   This still exercises role-shaped tool selection because the runtime reads `actor.role`.
2. **Real login as `op1` (stricter).** Seed the user store with `op1`, `POST /api/auth/login`
   with op1's credentials, and pass the returned cookie on every subsequent request. Use this
   if the smoke should also cover the cookie/login-session path.

Either way, the actor must carry `Role.OPERATOR` so `can_propose` derives `True` and the
purchasing/order-manager cases can reach `request_approval`. Do not pass a `RuntimeActor`
to the override — session routes read `actor.role`/`actor.username` and will break.

Each case should assert:

- request completes before the per-case timeout
- an `agent_answer` or `agent_proposal` is appended
- expected specialist appears in route trace
- required tools are present (see §4 semantics; "expected tools" is not a menu)
- forbidden tools are absent
- no repeated-tool fanout beyond the case budget
- grounding authority matches the expected class
- artifact/proposal presence matches the case

Suggested location:

```text
tests/integration/test_demo_live_smoke.py
```

### 3.3 Tier 2 — Browser Smoke

Deferred by default.

Add only if Playwright or equivalent browser dependencies are already available locally without
new setup friction. The first browser smoke should be tiny:

- login as `op1`
- send one chart prompt
- verify an artifact appears inside the chat thread
- send one purchasing prompt
- verify an approval card appears and status updates after approve/reject

The API smoke remains the required gate. Browser smoke is useful for UI wiring, but it should not
block backend slices until it is stable and cheap to run.

## 4. Canonical Live Cases

Keep the initial set small. These are the demo paths that have surprised us in manual testing.

Column semantics — do NOT treat the table as a loose "any tool satisfies the case":

- **Expected Tools** = `allowed` set. Any tool listed may appear; tools outside this set
  (and outside the forbidden set) are not auto-failures, but the case is only satisfied if
  the **Required Tools** below also fired.
- **Forbidden Tools** = `forbidden` set. Any appearance is an immediate failure.
- **Required Tools** (list below the table) = `all_of` set. Every listed tool MUST appear
  at least once, or the case fails. This is what stops a weak validation read from passing
  the case — e.g. `purchase_order_proposal` is not satisfied unless `request_approval`
  actually fired.
- Budget-style constraints on repeated reads (e.g. "no brute-force `order_query` loop") are
  neither allowed nor forbidden here — they live in §5.

| ID | Prompt | Expected Specialist | Expected Tools (allowed) | Forbidden Tools | Expected Output |
| --- | --- | --- | --- | --- | --- |
| `inventory_low_stock_sku` | `is SKU-LOW-003 below safety stock?` | `inventory` | `product_search`, `inventory_low_stock`, `inventory_query` | write tools, viz tools | authoritative answer with stock/safety numbers |
| `customer_top_spend` | `who are our top customers by spend?` | `customer-insights` | `get_statistics`, `user_query`, `order_query` | write tools, `task`, `write_todos` | authoritative answer; brute-force per-customer loop is bounded by §5, not forbidden outright |
| `sales_category_chart` | `compare sales by category and chart it` | `sales-analyst` | `get_statistics`, one suitable chart tool, `stage_sales_analysis_inputs`, `execute` | `generate_line_chart` for category-only comparison, write tools | authoritative answer with chart artifact |
| `forecast_chart` | `forecast SKU-LOW-003 sales next month and chart it` | `sales-analyst` | `stage_sales_analysis_inputs`, `execute`, one chart tool, `get_statistics` | write tools | derived or authoritative answer with chart artifact |
| `purchase_order_proposal` | `create a purchase order for 200 units of productId 9 from supplier 7` | `purchasing` | `product_search`, `supplier_query`, `supplier_top`, `purchase_order_query`, `request_approval` | direct write tools (`purchase_order_create`, `purchase_order_receive`, `order_update`) | pending proposal card |
| `order_status_change` | `cancel order 1234` | `order-manager` | `order_query`, `request_approval` | direct write tools (`order_update`), viz tools, `get_statistics` | pending proposal card for the status change |
| `invalid_sku_graceful` | `forecast SKU-NOPE-999 next month and chart it` | `sales-analyst` or `inventory` | product lookup/read tools | write tools, chart artifact unless real data exists | graceful no-data answer; no long loop |

Required Tools (`all_of`, must each appear at least once):

- `inventory_low_stock_sku`: `inventory_low_stock` OR `inventory_query` (any_of) — these
  are the only tools that return stock/safety facts. `product_search` is allowed but does
  NOT satisfy the case on its own; it only resolves product identity, so a case that calls
  only `product_search` must fail.
- `customer_top_spend`: `get_statistics` (the aggregate path — no per-customer loop substitute).
- `sales_category_chart`: `get_statistics` AND exactly one chart tool from `VIZ_TOOL_NAMES`
  (bar/column/pie/treemap per §7).
- `forecast_chart`: `stage_sales_analysis_inputs` AND `execute` AND a chart artifact (artifact
  may come from the sandbox path or a viz tool per §7).
- `purchase_order_proposal`: `request_approval` (without it, no proposal exists).
- `order_status_change`: `request_approval` (without it, no proposal exists).
- `invalid_sku_graceful`: none required beyond graceful termination within budget.

The expected specialist may be adjusted only if the registry descriptions intentionally change.

## 5. Repeated-Tool Fanout Guard

Several failures were not wrong because a tool was called once; they were wrong because the model
called the same read tool over and over instead of using the aggregate path.

Add case-level budgets:

```text
max_total_tool_calls: 12
max_same_tool_calls:
  order_query: 2
  user_query: 2
  product_query: 2
  product_search: 2
  inventory_query: 2
```

`product_search` is listed alongside `product_query` because inventory, purchasing,
and customer-insights expose only `product_search` (only sales-analyst selects
`product_query` via the `spring.read` tag) — a runaway loop on `product_search`
would otherwise pass.

For sandbox/chart cases, allow repeated `execute` only if the turn still completes within timeout
and produces the expected artifact. Direct specialists should never call DeepAgents scaffolding
tools. This is already structurally enforced by `_PLANNING_EXCLUDED_TOOLS` in
`src/ecommerce_agent/agents.py` (and covered by `tests/test_agents.py`); the smoke
acts as a regression tripwire in case that exclusion breaks:

```text
forbidden_always:
  - task
  - write_todos
```

## 6. Grounding Rules to Assert

The smoke should catch badge regressions directly:

- `get_statistics` aggregate answers: `authoritative`
- `inventory_query` / `inventory_low_stock` factual inventory answers: `authoritative`
- sandbox `execute` with evidence and no aggregate: `derived`
- numeric claims without data-bearing sources: `unverified`
- no numeric/data claim: no badge or `not_applicable`

The smoke should inspect the persisted message grounding payload, not only the visible text.

## 7. Chart Assertions

Do not judge chart aesthetics in code. Do assert the basics that prevent the worst demo failures:

- exactly one chart artifact is attached when the prompt asks for a chart
- when a viz tool from `VIZ_TOOL_NAMES` is used, the chart tool is appropriate
  for the data shape. Note: the sandbox `execute` path can produce an artifact
  *without* a viz tool call (see `tests/integration/test_hero_live_smoke.py`),
  so this check only applies when a viz tool actually fired:
  - category comparison: bar/column/pie/treemap, not line
  - time trend/forecast: line/area/dual-axis is acceptable
  - top-N ranked list: bar/column is acceptable
- the artifact has a downloadable/renderable URL or payload metadata
- chart tools are absent for no-data prompts

## 8. Commands

Default deterministic smoke (skips cleanly when Spring/chart MCP are down):

```bash
uv run pytest tests/integration/test_demo_contract_smoke.py -q
```

Strict closeout gate (services-down is a FAILURE, not a skip). Use this in the closeout
run and in any CI gate that is supposed to actually prove the contract — it prevents a
false green where every check skipped because MCP was unreachable:

```bash
RUN_DEMO_CONTRACT_SMOKE=1 uv run pytest tests/integration/test_demo_contract_smoke.py -q
```

This mirrors the existing opt-in convention (`RUN_MONGO_INTEGRATION`,
`RUN_M2_APPROVAL_INTEGRATION`) but inverted: when set, unreachable Spring MCP or chart MCP
must fail the run rather than skip.

Live API smoke:

```bash
RUN_LIVE_LLM=1 uv run pytest tests/integration/test_demo_live_smoke.py -q
```

Recommended pre-merge quick gate after prompt/tool/catalog changes:

```bash
uv run pytest tests/test_prompts.py tests/test_specialists.py tests/test_tool_metadata.py \
  tests/integration/test_demo_contract_smoke.py -q
```

Recommended live gate before demo or after large behavior changes:

```bash
RUN_LIVE_LLM=1 uv run pytest tests/integration/test_demo_live_smoke.py -q
```

## 9. Timeouts and Diagnostics

Each live case should have a hard timeout, default `150s`. The sandbox case
(`forecast_chart`, whose required tools include `execute`) can spend 30-60s on
container warm-up alone, so do not lower this below 120s for cases that touch
`execute`. `sales_category_chart` does not require `execute` (its required tools are
`get_statistics` plus a viz chart tool), so it is not a sandbox-warm-up case. The
existing hero smoke uses 180s as a reference point.

On failure, write a compact diagnostic JSONL under `.pytest_cache/` with:

- case id and prompt
- session id and turn id
- answer/proposal tail
- route decision
- ordered tool names
- repeated tool counts
- sandbox activity breakdown — counts of `execute` and `stage_sales_analysis_inputs`,
  plus total sandbox wall time. This makes the "model overdid sandbox activity on a
  case that doesn't require it" failure mode (e.g. `sales_category_chart`, where
  sandbox tools are allowed but not required) immediately obvious versus just being
  buried in the ordered tool list.
- grounding payload
- artifact/proposal summary
- trace path if exported

The diagnostic file is for local debugging only and should not be committed.

## 10. Closeout Criteria

- Tier 0 contract smoke passes with local Spring MCP and chart MCP running, under
  `RUN_DEMO_CONTRACT_SMOKE=1` (so a skip is treated as failure and the gate cannot
  report a false green).
- Tier 1 live API smoke skips cleanly without `RUN_LIVE_LLM=1`.
- Tier 1 live API smoke passes against the configured live model when explicitly enabled.
- The smoke catches at least these known regressions:
  - missing `topCustomersBySpend` aggregate
  - `inventory_low_stock` answer marked `unverified`
  - customer spend prompt brute-forces many `order_query` calls
  - category chart uses a line chart
  - regression: direct specialist gains access to `task` or `write_todos`
    (currently excluded by `_PLANNING_EXCLUDED_TOOLS` in `agents.py`)
  - purchasing prompt calls direct write tools instead of `request_approval`

## 11. Open Questions

1. Should the live smoke mutate real approvals, or use a fresh test session and leave pending
   proposals for cleanup? Default: fresh session; no approve/execute in Tier 1.
2. Should browser smoke land in this slice? Default: defer unless it can be added without new
   dependency setup.
3. Should live smoke be one test with all cases or one test per case? Default: one test per case
   so failures are readable and rerunnable.
