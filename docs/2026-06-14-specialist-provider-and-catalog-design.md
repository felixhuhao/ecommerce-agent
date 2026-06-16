# Specialist Provider + Catalog Design

## 1. Goal

Two coupled goals:

1. **Define the specialist catalog** for this internal back-office e-commerce platform, grounded
   in how real platforms (Shopify, Amazon Seller Central, NetSuite) divide operator roles, so we
   build toward a known full picture instead of one specialist at a time.
2. **Introduce a `SpecialistProvider` abstraction** so adding a specialist is a single registration,
   not bespoke wiring copy-pasted across `agents.py`, `mcp_client.py`, `factory.py`, and the router
   registry.

Non-goal: activating the dormant coordinator / multi-agent-within-a-turn topology. Routing stays
single-hop (one specialist per turn). The dormant coordinator seam in `agents.py` is preserved, not
used.

## 2. Background: how the wiring works today

- Routing is clean: `SpecialistRegistry` holds descriptors; `ClassifierRouter` builds its prompt from
  `registry.describe()`; `RoutedSessionAgent` does one classifier call, picks an agent from a
  `dict[str, Any]`, and delegates `astream_events`.
- The **build path is hand-wired**. Each specialist needs, across separate files: a `build_X` factory
  (`agents.py`), a tool filter + frozenset (`mcp_client.py`), a prompt entry in the single
  `prompts/prompts.yml` (loaded by the key-based `prompts/loader.py`), instantiation in
  `build_session_runtime` (`factory.py`), an entry in `build_role_shaped_agents` (hardcoded
  `sales-analyst` always-on + `order-manager` if `can_propose`), and a descriptor in the registry.
- **Tool→specialist allocation is frozenset sprawl**: `READ_ONLY_SPRING_TOOLS`, `ORDER_MANAGER_SPRING_TOOLS`,
  `WRITE_SPRING_TOOLS`, `APPROVAL_SPRING_TOOLS`, `VIZ_TOOLS`, plus the union
  `WRITE_OR_APPROVAL_SPRING_TOOLS`. Only `READ_ONLY_SPRING_TOOLS`, `ORDER_MANAGER_SPRING_TOOLS`, and
  `VIZ_TOOLS` are consumed by a `filter_*` function; `WRITE_SPRING_TOOLS`/`APPROVAL_SPRING_TOOLS` exist
  only for structural/test assertions and are never handed to an agent directly.
- **Tool-name behavior is keyed in several scattered maps**: `DATA_BEARING_TOOLS` (`trace/tools.py`),
  `VIZ_TOOLS` (artifact capture), `request_approval` special-casing (approval-id extraction), and the
  slice-8 live-status label logic (`sessions/turn.py:_tool_label`). Adding a tool means remembering to
  touch each relevant one, and they drift.

Adding specialist #3 today touches ~6 files of bespoke wiring. That is the cost this design removes.

## 3. Specialist catalog

Carved on the **function × risk hybrid**: a *function* names the specialist; a *capability* tier
(`read` | `propose`) is an attribute that drives tool selection and the role gate. This mirrors Amazon
Seller Central's `section × access-level` model and NetSuite's separation of the doer (Buyer) from the
approver. The human operator is the approver in our HITL model; "propose" specialists never write
directly — they request approval.

### 3.1 Near-term set (buildable on current Spring tools)

> **Target catalog.** This table shows ownership after Phases B/C land. Phase A is behavior-preserving:
> the `order-manager` provider keeps today's full `ORDER_MANAGER_SPRING_TOOLS` (reads +
> `request_approval`) until Phase B re-homes the PO/supplier reads.

| Specialist | Real-world analog | Tier | Tool/operation surface |
|---|---|---|---|
| `sales-analyst` *(exists)* | Report Viewer / Analytics | read | `get_statistics`, all read queries, viz, sandbox |
| `order-manager` *(exists)* | Order Manager | propose | reads: `order_query`; approvals: `order_update` |
| `purchasing` *(new)* | Buyer / Procurement | propose | reads: `supplier_query`, `supplier_top`, `purchase_order_query`; approvals: `purchase_order_create`, `purchase_order_receive` |
| `inventory` *(new)* | Inventory Manager | read | `inventory_query`, `inventory_low_stock` |
| `customer-insights` *(new)* | CRM | read | `user_query`, `order_query`, `get_statistics` |

**PO ownership moves out of `order-manager` into `purchasing`.** Real platforms separate fulfillment
(order status) from procurement (supplier + PO). Today `order-manager` holds `product_query`,
`inventory_query`, `supplier_query`, and `purchase_order_query` reads alongside `order_query`
(`mcp_client.py:ORDER_MANAGER_SPRING_TOOLS`); after this change `order-manager` keeps only `order_query`
plus the approval operation contract for `order_update`. Losing those extra reads is an intended
boundary-tightening, not just PO relocation: the current `order-manager` prompt instructs the model to
use `product_query`/`inventory_query`/`supplier_query`/`purchase_order_query` for fact-gathering, so
Phase B must trim that prompt text to match the new tool set.

Important: propose specialists do **not** receive direct write MCP tools. They receive
`request_approval` plus the read tools needed to validate the proposal, and their provider declares
which approval operation contracts they may request. The Java/Spring approval service remains the only
place that executes `order_update`, `purchase_order_create`, or `purchase_order_receive` after human
approval.

### 3.2 Backend-gated (defined in the catalog, not buildable until Spring adds tools)

| Specialist | Real-world analog | Blocked on |
|---|---|---|
| `catalog-manager` | Listings Manager / Merchandiser | product/pricing **write** tools (only `product_query/search` exist) |
| `finance` | A/P Clerk / Finance | payment/reconciliation tools (only `get_statistics` exists) |

These exist in the catalog so the abstraction is designed to host them; they are not wired until tools
land.

### 3.3 Use cases (also serve as routing-eval seeds)

**`sales-analyst` (read)** — business-centric numbers and trends.
- "What were last month's sales vs the month before?"
- "Show a chart of revenue by category for Q2."
- "Forecast next month's revenue from recent trend."
- Output: grounded answer + chart artifact.

**`order-manager` (propose)** — order status and fulfillment.
- "Mark order 4821 as shipped." / "Cancel order 5012."
- "What's the status of orders placed yesterday?" (read path)
- Output: reads → answer; status change → approval proposal.

**`purchasing` (propose)** — suppliers and purchase orders.
- "Create a PO for 200 units of SKU-119 from our main supplier."
- "Who are our top suppliers by volume?" / "Receive PO 338."
- Output: reads → answer; create/receive → approval proposal.

**`inventory` (read)** — stock health; also the read surface for the slice-7 proactive monitor.
- "What's below reorder point right now?" / "Stock for SKU-119?"
- "Which SKUs risk stockout this week?"
- Output: answer. Flags and recommends; never reorders.

**`customer-insights` (read)** — customer-centric analytics.
- "Top customers by spend?" / "Customer 88's order history."
- "Repeat vs one-time buyers last quarter?"
- Output: answer.

### 3.4 Routing boundaries (the risks to test)

- **Three read specialists share `get_statistics`** (`sales-analyst`, `inventory`, `customer-insights`).
  The classifier disambiguates on **subject**: business (revenue/products/trends) vs stock vs customer.
  This is the softest boundary; it needs sharp prompt framing and a routing-eval case per pair.
- **`inventory` → `purchasing` is the one genuine cross-specialist workflow.** `inventory` observes
  ("low on X, recommend reorder"); `purchasing` acts ("create the PO"). Single-hop routing means the
  *user* bridges it across two turns — acceptable, and arguably safer for an approval-gated action
  than auto-delegation. No coordinator required.
- **`order-manager` vs `purchasing`**: order status vs supplier/PO. Clean once PO tools move.

## 4. The `SpecialistProvider` abstraction

A provider bundles everything needed to register, route to, build, and gate one specialist. The
session factory iterates providers instead of hand-wiring each.

```python
@dataclass(frozen=True)
class SpecialistProvider:
    name: str                      # "purchasing"
    description: str               # router-facing; feeds registry.describe()
    capability: Literal["read", "propose"]
    prompt_key: str                # prompts/ loader key
    tool_tags: frozenset[str]      # fine-grained read/viz/custom tool tags this specialist gets
    approval_operations: frozenset[str] = frozenset()  # proposed write operation names, not tools
    default: bool = False          # exactly one provider is the routing default

    def is_enabled(self, actor: RuntimeActor) -> bool:
        # read specialists: always; propose specialists: actor.can_propose
        return self.capability == "read" or actor.can_propose

    def build(self, *, model, tools_for_specialist, backend, staging_tools=()) -> Any:
        ...  # wraps build_agent with the right prompt + middleware
```

- `SpecialistRegistry` is constructed **from all routeable providers** (`describe()` derives from their
  `name`/`description`), removing the parallel hand-maintained registry in `routing/registry.py`.
- Runtime agent construction is role-shaped: `{p.name: p.build(...) for p in providers if p.is_enabled(actor)}`.
  The router registry still includes propose specialists for viewers, so a write-intent can route to
  the omitted specialist and produce the existing policy-denial answer. Do **not** build the router
  registry from only enabled providers.
- Adding a specialist = add one `SpecialistProvider` (+ its prompt). No edits to `factory.py` control flow.

## 5. Tool metadata (replacing frozenset sprawl)

Introduce a single per-tool metadata table; specialists select tools by tag, and the
scattered name-keyed maps read from it.

```python
@dataclass(frozen=True)
class ToolMeta:
    name: str
    source: Literal["spring", "modelscope", "custom", "backend"]
    tags: frozenset[str]           # e.g. {"orders.read"}, {"approval.request"}, {"viz.chart"}
    data_bearing: bool = False     # feeds grounding evidence
    live_label_start: str | None = None  # operator-facing label while the tool is running
    live_label_end: str | None = None    # operator-facing label at tool end (e.g. "Chart generated")
```

- A provider's `tool_tags` intersect with each tool's `tags` to produce its tool set — replacing
  `filter_spring_read_tools`, `filter_order_manager_tools`, etc.
- Tags must be fine-grained enough to preserve today's scoped tool sets. A single coarse `read` tag is
  not acceptable because it would give `order-manager` unrelated read tools such as `get_statistics`,
  `user_query`, `supplier_top`, or `inventory_low_stock`. Use tags such as `orders.read`,
  `products.read`, `inventory.read`, `suppliers.read`, `purchase_orders.read`, `analytics.aggregate`,
  `customers.read`, `viz.chart`, `approval.request`, and `analysis.staging`.
- The tag taxonomy list above is illustrative, but the Phase A `ToolMeta` table must be exhaustive over
  today's read set: `product_query`, `product_search`, `order_query`, `inventory_query`,
  `inventory_low_stock`, `user_query`, `supplier_query`, `supplier_top`, `purchase_order_query`, and
  `get_statistics`. It must also cover the direct write tools `order_update`, `purchase_order_create`,
  and `purchase_order_receive`: these are never selected for agents, but they are referenced by API
  diagnostics (`api/app.py`) and the reliability eval (`evals/live_reliability.py`), and acceptance says
  one table answers "what kind of tool is this." Distinguish tools when behavior differs, e.g.
  `products.query` vs `products.search`.
- Propose specialists select the `approval.request` tool (`request_approval`) through tags, but their
  allowed write surface is `SpecialistProvider.approval_operations`; direct write tools are not passed
  to agents.
- In Phase A, `approval_operations` is declarative metadata only. It documents each propose
  specialist's intended write surface and prepares Phase B/C ownership checks; enforcement continues
  to come from prompts + Java approval contracts + not exposing direct write tools.
- `is_data_bearing(name)` reads `ToolMeta.data_bearing` instead of `DATA_BEARING_TOOLS`.
- Viz/artifact handling reads the `viz.chart` tag instead of `VIZ_TOOLS`.
- The slice-8 live-status label logic reads `ToolMeta.live_label_start`/`live_label_end` instead of the
  standalone `_tool_label` conditionals in `sessions/turn.py`. Two fields are required because today's
  `_tool_label` is phase- and artifact-conditional, not a flat name→label map: viz tools show
  "Generating chart" while running then "Chart generated" at end, and `request_approval` shows
  "Requesting approval" then "Approval requested". A single `live_label` string cannot reproduce these.
  The phase/artifact dispatch stays in `turn.py`; only the strings move to `ToolMeta`.
- `ToolMeta.source` keeps build mechanics explicit: Spring/ModelScope tools are MCP-discovered,
  `stage_sales_analysis_inputs` is custom-built with Spring read tools + sandbox backend, and `execute`
  is backend-injected by DeepAgents rather than MCP-discovered. Phase A centralizes classification and
  selection metadata; provider build hooks may still construct custom/backend tools where needed.

This centralizes the "what kind of tool is this" question that is currently answered in four places,
and reduces privilege-leak risk by making tool classification a single source of truth instead of a
set of hand-maintained frozensets.

> Coordination note: slice 8 (live turn status) added the `_tool_label` function in
> `sessions/turn.py`, which is phase- and artifact-conditional (not a flat name→label map). Phase A
> migrates the label strings into `ToolMeta.live_label_start`/`live_label_end`; the phase dispatch
> remains in `turn.py`.

## 6. Phasing

This is too large for one implementation plan. Phase it:

- **Phase A — abstraction only (refactor, zero behavior change).** Introduce `SpecialistProvider` and
  `ToolMeta`; re-express today's `sales-analyst` + `order-manager` as providers; collapse
  `build_role_shaped_agents` and the registry. Proves the seam with no routing-ambiguity risk. All
  existing tests stay green; routing evals unchanged.
- **Phase B — PO re-ownership + `purchasing`.** Move `purchase_order_*` + `supplier_*` from
  `order-manager` to a new `purchasing` provider. Add routing-eval cases for the order/purchasing
  boundary.
- **Phase C — read specialists.** Add `inventory` and `customer-insights` providers. Add per-pair
  routing-eval cases for the three `get_statistics` sharers (this is the real risk; gate the phase on
  routing-eval accuracy).
- **Later (backend-gated).** `catalog-manager`, `finance` when Spring exposes the tools.

Phase A is the foundation slice. B and C are independent follow-ups, each its own plan.

## 7. Scope (Phase A)

In scope:
- `SpecialistProvider` dataclass + `build` wrapper over `build_agent`.
- `ToolMeta` table + tag-based tool selection.
- Registry derived from all routeable providers, while runtime agents remain role-shaped.
- `factory.py` iterates providers; delete hardcoded role if/else.
- Re-point `is_data_bearing` and viz handling at `ToolMeta`.

Out of scope (Phase A):
- New specialists (B/C).
- PO re-ownership (B).
- Coordinator / multi-hop routing.
- Backend-gated specialists.
- The `monitor_cause` agent (`agents.py:build_monitor_cause_agent`, its `_MONITOR_CAUSE_EXCLUDED_TOOLS`
  frozenset, and the `monitor_cause` prompt). It is built via `build_agent` but wired through the
  proactive monitor, not the router, so it is intentionally outside the `SpecialistProvider`
  abstraction. Phase A leaves it untouched.

## 8. Tests

Phase A (behavior-preserving):
- Provider registry produces the same two specialists and the same `describe()` text the classifier
  sees today (snapshot).
- `is_enabled` gates `order-manager` off when `actor.can_propose` is false, on when true — matching
  current `build_role_shaped_agents` behavior.
- Router registry still contains `order-manager` for viewers; viewer write-intent still produces the
  existing policy-denial answer instead of rerouting to the default specialist.
- Tag-based tool selection yields the same tool sets as today's `filter_*` functions
  (assert set-equality per specialist).
- Propose specialists receive `request_approval` and scoped reads, not direct write tools.
- `is_data_bearing` and viz/artifact capture behavior unchanged (existing trace tests stay green).
- The `_tool_label` progress-frame labels in `sessions/turn.py` unchanged — including the
  phase-conditional strings ("Generating chart"/"Chart generated", "Requesting approval"/"Approval
  requested") locked by `tests/test_session_turn.py`.
- Routing evals unchanged.

Phase B/C add: order/purchasing boundary eval cases; per-pair `get_statistics` disambiguation eval
cases; tool-ownership assertions (no tool in two specialists' propose sets unintentionally).

## 9. Open questions

1. Does `purchasing` own `purchase_order_query` (read) exclusively, or may `order-manager` keep read
   access to POs for fulfillment context?
   - Default: `purchasing` owns it exclusively; cleaner boundary, matches real platforms. Phase B must
     also update the `order-manager` prompt, which currently instructs the model to call
     `purchase_order_query`/`supplier_query` for fact-gathering.
2. Should `inventory` reuse the `sales-analyst` prompt scope-limited, or get its own prompt?
   - Default: own prompt — subject framing is exactly what keeps the `get_statistics` boundary sharp.
3. Should `ToolMeta` live in `mcp_client.py` or a new `tools/metadata.py`?
   - Default: new `tools/metadata.py`; `mcp_client.py` is already large and this is consumed by trace,
     grounding, and live-status code, not just the MCP client.

## 10. Acceptance (Phase A)

- Adding a specialist requires only a new `SpecialistProvider` (+ prompt), no `factory.py` control-flow
  edits.
- "What kind of tool is this" is answered from one `ToolMeta` table, not four scattered maps.
- No behavior change: identical specialists, tool sets, routing decisions, grounding, and traces.
- The hardcoded `sales-analyst`/`order-manager` role if/else is gone.
- Direct write MCP tools are not exposed to propose specialists; HITL remains mediated by
  `request_approval`.

## 11. Phase A implementation checklist

This section is the execution checklist; no separate implementation plan is required for Phase A.

- [ ] Add `tools/metadata.py` with `ToolMeta`, exhaustive metadata for current Spring read tools, the
  direct write tools (`order_update`, `purchase_order_create`, `purchase_order_receive`),
  `request_approval`, ModelScope viz tools, staging, and backend `execute`.
- [ ] Add tag-selection helpers that reproduce today's `filter_spring_read_tools`,
  `filter_order_manager_tools`, and `filter_viz_tools` outputs exactly.
- [ ] Add `specialists/providers.py` with `SpecialistProvider` definitions for current
  `sales-analyst` and `order-manager` only.
- [ ] Build `SpecialistRegistry` from all routeable providers; keep viewer write-intent policy denial
  by role-shaping runtime agents, not the router registry.
- [ ] Refactor `build_session_runtime` to iterate enabled providers and remove the hardcoded
  `sales-analyst` / `order-manager` construction branch.
- [ ] Repoint `trace/tools.py` (`DATA_BEARING_TOOLS`) and artifact capture (`VIZ_TOOLS`) to `ToolMeta`.
- [ ] Migrate API diagnostics (`api/app.py` imports `WRITE_SPRING_TOOLS`, `APPROVAL_SPRING_TOOLS`,
  `READ_ONLY_SPRING_TOOLS`, `ORDER_MANAGER_SPRING_TOOLS`, `VIZ_TOOLS`) and the reliability eval
  (`evals/live_reliability.py` imports `VIZ_TOOLS`, `WRITE_OR_APPROVAL_SPRING_TOOLS`) to read from
  `ToolMeta`/compatibility helpers; do not leave the old frozensets half-migrated.
- [ ] Migrate slice-8 `_tool_label` strings to `ToolMeta.live_label_start`/`live_label_end`; keep the
  phase/artifact dispatch in `sessions/turn.py`; assert the phase-conditional labels stay
  byte-identical to today.
- [ ] Keep direct write tools out of provider tool sets; `approval_operations` remains declarative in
  Phase A.
- [ ] Run behavior-preserving tests: provider registry snapshot, role gating, tool set equality,
  policy-denial regression, trace/artifact/grounding tests, routing evals.
