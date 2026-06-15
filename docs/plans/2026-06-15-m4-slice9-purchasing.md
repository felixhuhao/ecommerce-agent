# Phase B: `purchasing` specialist + PO re-ownership

> **Execution:** inline, TDD per task. Verification bar = unit suite + ruff (live routing &
> approval evals run by the user afterward). Branch `m4-slice9-purchasing`, stacked on
> `m4-slice8-specialist-provider` (Phase A).

**Goal:** Add a `purchasing` specialist that owns supplier + purchase-order reads and
PO create/receive approvals; narrow `order-manager` to order-status reads + `order_update`;
re-align prompts, routing eval, and approval-safety eval to the new boundary.

**Source of truth:** `docs/2026-06-14-specialist-provider-and-catalog-design.md` §3.1, §3.3, §3.4, §6.

---

## Scope

In:
- New `purchasing` provider: reads `supplier_query`, `supplier_top`, `purchase_order_query`;
  approvals `purchase_order_create`, `purchase_order_receive`.
- `order-manager` narrowed: reads `order_query` only; approvals `order_update` only.
  Loses `product_query`, `inventory_query`, `supplier_query`, `purchase_order_query`.
- Prompts: new `purchasing`; narrowed `order_manager`; rewritten `router_classifier`
  (3-way: sales-analyst / order-manager / purchasing).
- `routing.yaml`: re-label the 5 PO/supplier cases to `purchasing`; add order↔purchasing
  boundary cases.
- `approval_safety`: per-case `specialist` field; purchasing stub; runner groups by specialist.
- `mcp_client` shim: `ORDER_MANAGER_SPRING_TOOLS` narrows to track the provider; add
  `PURCHASING_SPRING_TOOLS`.

Out:
- `inventory` and `customer-insights` specialists (Phase C).
- Backend-gated specialists.
- Coordinator / multi-hop routing.

## Behavior change (not preserving)

Phase B is a **behavior-changing** slice (unlike Phase A). Known changes:
- order-manager can no longer answer product/inventory/supplier/PO questions (those route
  to purchasing or, for inventory, sales-analyst until Phase C).
- PO/supplier write-intent routes to purchasing, not order-manager.
- Viewer write-intent still policy-denied (purchasing is propose → omitted for viewers;
  router registry still lists it).

---

## File map

| File | Change |
|---|---|
| `src/ecommerce_agent/specialists/providers.py` | Add `purchasing` provider + `_assemble_purchasing`; narrow `ORDER_MANAGER_TAGS` to `{orders.query, approval.request}`; set `ORDER_MANAGER_APPROVAL_OPERATIONS={order_update}`. |
| `src/ecommerce_agent/prompts/prompts.yml` | Add `purchasing` prompt (relocate PO/supplier guidance); strip PO/supplier/product/inventory from `order_manager`; rewrite `router_classifier` for 3-way. |
| `src/ecommerce_agent/evals/datasets/routing.yaml` | Re-label 5 cases → `purchasing`; add boundary cases. |
| `src/ecommerce_agent/evals/datasets/approval_safety.yaml` | Add `specialist` per case; reassign PO→purchasing, order→order-manager; drop the inventory read case (now a sales-analyst read, not propose-safety). |
| `src/ecommerce_agent/evals/approval_safety.py` | Add `build_stub_purchasing`/`build_stub_purchasing_tools`; `ApprovalCase.specialist`; helper to build the right stub per specialist. |
| `src/ecommerce_agent/mcp_client.py` | Narrow `ORDER_MANAGER_SPRING_TOOLS` (derive from order-manager provider tags); add `PURCHASING_SPRING_TOOLS`. |
| `tests/test_specialists.py` | purchasing provider assertions; order-manager narrowed approval_operations. |
| `tests/test_mcp_client.py` | Update `filter_order_manager_tools` assertion (no longer keeps product/inventory). |
| `tests/test_approval_safety.py` | Update surface assertions; add purchasing stub tests; specialist-field loading. |
| `tests/integration/test_approval_safety_live.py` | Group cases by specialist; build matching stub per group. |

---

## Tasks

### Task 1: `purchasing` provider + order-manager narrowing

TDD in `tests/test_specialists.py`:
- `PROVIDERS` is now `[sales-analyst, order-manager, purchasing]`; still exactly one default.
- `purchasing`: capability `propose`; `approval_operations == {purchase_order_create, purchase_order_receive}`;
  `prompt_key == "purchasing"`; `is_enabled` gated on `can_propose`.
- `purchasing.tool_tags` selects `{supplier_query, supplier_top, purchase_order_query, request_approval}`
  and excludes writes + order_query + product/inventory.
- order-manager `approval_operations == {order_update}`; `tool_tags` selects `{order_query, request_approval}`.

Impl (`specialists/providers.py`):
- `PURCHASING_TAGS = {suppliers.query, suppliers.top, purchase_orders.query, approval.request}`.
- `ORDER_MANAGER_TAGS` → `{orders.query, approval.request}`.
- `_assemble_purchasing(model, spring_tools, viz_tools, selected_names, backend)` →
  `build_order_manager`-style: no staging; tools = selected spring tools. (Reuses
  `build_order_manager` factory — same shape: reads + request_approval, no sandbox.)
- `ORDER_MANAGER_APPROVAL_OPERATIONS = {order_update}`.
- Append purchasing `SpecialistProvider` (non-default).

Commit: `feat(specialists): add purchasing provider, narrow order-manager to order status`.

### Task 2: Prompts

`prompts.yml`:
- **`purchasing`** (new): Procurement specialist. Reads supplier/PO for facts; requests approval
  for `purchase_order_create` / `purchase_order_receive`. Relocate the create/receive operation
  contracts and the unitCost discipline (omit unitCost; Java canonicalizes from product cost;
  never infer from price/revenue/inventory) from the current order_manager prompt. productId
  comes from `supplier_query` products (purchasing has no `product_query`).
- **`order_manager`** (narrowed): Order-status specialist. Keep the read-then-propose workflow
  but only for `order_query` + `order_update`. Remove all PO/supplier/product/inventory guidance
  and the create/receive contracts.
- **`router_classifier`** (rewrite): 3-way. sales-analyst = read-only analytics. order-manager =
  customer-order status changes (ship/cancel/update). purchasing = procurement (create/receive
  PO, restock, replenish, suppliers). Keep the "analyze vs act" disambiguation; add order-vs-PO.

TDD: a `tests/test_prompts.py` (or extend existing) asserting: `purchasing` prompt loads,
mentions `purchase_order_create`/`purchase_order_receive`; `order_manager` prompt no longer
mentions `purchase_order_create`; `router_classifier` mentions `purchasing`.

Commit: `feat(prompts): add purchasing prompt, narrow order-manager, split router classifier`.

### Task 3: Routing eval dataset

`routing.yaml`:
- Re-label to `purchasing`: `create-po`, `receive-po`, `buy-more-units`, `low-stock-reorder`,
  `replenish-supplier`.
- Keep `order-manager`: `update-order-status`.
- Add boundary cases (sales-analyst/purchasing/order-manager):
  - `po-volume-report` already sales-analyst (keyword-false-positive) — keep.
  - new `order-status-read` ("what's the status of order 8812?") → order-manager.
  - new `po-status-read` ("what's the status of PO 4471?") → purchasing.
  - new `top-suppliers` ("who are our top suppliers by volume?") → purchasing.
  - new `cancel-order` ("cancel order 5012") → order-manager.
  - new `supplier-report` ("report on supplier lead times") → sales-analyst (analyze vs act).

TDD: `load_routing_cases()` validates all `expected` against the registry (purchasing now
registered); assert the re-labeled cases expect `purchasing`.

Commit: `test(routing): re-label PO cases to purchasing, add boundary cases`.

### Task 4: `mcp_client` shim narrowing

- `ORDER_MANAGER_SPRING_TOOLS` → derive from order-manager provider's tool_tags
  (`select_names({orders.query, approval.request})` = `{order_query, request_approval}`),
  so the shim auto-tracks the provider. Add comment that it mirrors the provider.
- Add `PURCHASING_SPRING_TOOLS = select_names({suppliers.query, suppliers.top, purchase_orders.query, approval.request})`.
- Update `tests/test_mcp_client.py::test_filter_order_manager_tools_*`: kept set is now
  `{order_query, request_approval}` (drop product/inventory/supplier/PO from assertions).
- Update `tests/test_approval_safety.py::test_order_manager_surface_*` and
  `test_filter_drops_write_tools_*` to the narrowed surface; add purchasing surface test.

Commit: `refactor(mcp_client): narrow ORDER_MANAGER shim, add PURCHASING shim`.

### Task 5: approval-safety eval restructure

`approval_safety.py`:
- `ApprovalCase.specialist: str` field (default `"order-manager"` for back-compat).
- `build_stub_purchasing_tools(approval_calls)`: request_approval + supplier_query,
  supplier_top, purchase_order_query — the purchasing live surface only (no
  product_query; per catalog §3.1). productId comes from supplier_query.
- `build_stub_purchasing(settings, approval_calls)` → `build_purchasing(model, purchasing_tools=..., backend=None)` on purchasing stub tools.
- Helper `build_stub_for_specialist(specialist, settings, approval_calls)`.

`approval_safety.yaml`: add `specialist`. Purchasing write/read prompts use `productId`
(not SKU), since purchasing has no product_query and supplier_query does not return
SKU-bearing records:
- `po-create-product9`, `replenish-product3`, `receive-po-4471`, `read-suppliers-product3`,
  `read-open-pos` → `purchasing`.
- `read-order-status` → `order-manager`; add `update-order-status` write case → `order-manager`.
- Drop `read-inventory-sku9` (inventory read is a sales-analyst case now, not propose-safety).

`tests/integration/test_approval_safety_live.py`: group cases by `specialist`, build the
matching stub, run each group, aggregate. Keep the `false_proposal_rate == 0.0` and
`accuracy >= 0.80` gates over the union.

Unit tests: purchasing stub exposes request_approval + supplier/PO reads; specialist field
loads; default dataset balanced across specialists.

Commit: `refactor(evals): split approval-safety by specialist, add purchasing stub`.

### Task 6: Verify

- `ruff check src/ecommerce_agent/ tests/`
- `pytest -q -m "not docker and not integration and not live"`
- Smoke: `python -c "import ecommerce_agent.api.app"` (diagnostics still import).
- Note for user: run live routing eval (`RUN_LIVE_LLM=1`) + live approval-safety; review
  routing.yaml labels.

---

## Risks / watch-items

1. **order-manager capability reduction** — losing product/inventory reads may surface in
   approval_safety or hero smoke. Mitigated: order-manager only needs order_query for its
   narrowed scope.
2. **Classifier misroute** — router_classifier must split order vs purchasing or PO intents
   route to order-manager (which now lacks PO tools → would abstain incorrectly). The prompt
   rewrite in Task 2 is load-bearing; live routing eval confirms.
3. **approval_safety harness is a live eval** the user runs — keep the runner change minimal
   and the gates intact.
4. **product_query for PO creation** — purchasing has no product_query (per §3.1); productId
   is confirmed via supplier_query; unitCost omitted (Java canonicalizes). The approval-safety
   prompts use `productId` (not SKU) since supplier_query does not return SKU-bearing records —
   SKU prompts would have required inferring SKU→productId, which is not a real contract. The
   purchasing prompt tells the model to ask the operator if only a SKU is given.

## Self-review

- Spec coverage: §3.1 purchasing row → Task 1; order-manager narrowing → Task 1+2; §3.3
  purchasing use cases → Task 3 eval cases; §3.4 order↔purchasing boundary → Task 3; §6
  "add routing-eval cases" → Task 3; approval_safety PO re-home → Task 5. ✓
- order-manager approval_operations: §3.1 says `{order_update}` → Task 1 sets it. ✓ (Phase A
  had temporarily set 3 ops; Phase B narrows — update the Phase A test assertion.)
- No placeholders: prompt text authored in Task 2 execution; tool/eval contents enumerated.
