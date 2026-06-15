# Phase C — Read Specialists (inventory + customer-insights) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `inventory` and `customer-insights` read-only specialists to the routed catalog, expanding routing from 3-way to 5-way, with routing-eval cases that test the `get_statistics` subject boundary.

**Architecture:** Both are `read`-tier `SpecialistProvider` entries (always enabled, no `approval_operations`). Each gets its own prompt (own subject framing keeps the boundary sharp), its own `build_X` factory in `agents.py`, and scoped tool tags from the existing `ToolMeta` table. The router classifier prompt is rewritten for 5-way disambiguation by subject. No backend tool gaps — all tools already exist.

**Tech Stack:** Python 3.12, LangChain, PyYAML prompts, pytest.

**Branch:** `m4-slice10-read-specialists` off `m4-slice9-purchasing` (stacked).

**Design decisions (confirmed with user):**
- Both specialists added in one slice (per design doc §6).
- `sales-analyst` keeps all reads (`spring.read` tag) — it's the broad analytics specialist; no narrowing.
- `inventory` gets `inventory.query` + `inventory.low_stock` tags only (no `get_statistics` — per §3.1 table).
- `customer-insights` gets `customers.query` + `orders.query` + `analytics.aggregate` tags.
- Done bar: unit tests + authored routing cases; user runs live routing eval and we iterate.

---

## File Map

| File | Responsibility | Action |
|---|---|---|
| `src/ecommerce_agent/agents.py` | Agent factory functions | Add `build_inventory`, `build_customer_insights` + description constants |
| `src/ecommerce_agent/prompts/prompts.yml` | System prompts | Add `inventory`, `customer_insights` prompts; rewrite `router_classifier` for 5-way |
| `src/ecommerce_agent/specialists/providers.py` | Provider registry | Add tags, assemblers, 2 `PROVIDERS` entries |
| `src/ecommerce_agent/mcp_client.py` | MCP tool shims | Add `INVENTORY_SPRING_TOOLS`, `CUSTOMER_INSIGHTS_SPRING_TOOLS` + filter functions |
| `src/ecommerce_agent/api/app.py` | `/health/mcp` diagnostics | Add inventory + customer-insights tool visibility |
| `src/ecommerce_agent/evals/datasets/routing.yaml` | Routing eval cases | Re-label `inventory-snapshot`; add 9 new cases |
| `tests/test_specialists.py` | Provider assertions | Update PROVIDERS list; add inventory + customer-insights tests |
| `tests/test_routing_registry.py` | Registry snapshot | Update `describe()` snapshot + names set |
| `tests/test_routing_dataset.py` | Dataset validation | Update expected specialists set |
| `tests/test_prompts.py` | Prompt assertions | Add inventory + customer-insights tests; update router_classifier test |
| `tests/test_app.py` | `/health/mcp` test | Add inventory + customer-insights assertions |

---

### Task 1: Agent factories + prompts for inventory and customer-insights

**Files:**
- Modify: `src/ecommerce_agent/agents.py`
- Modify: `src/ecommerce_agent/prompts/prompts.yml`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing prompt tests**

Add to `tests/test_prompts.py`:

```python
def test_get_inventory_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("inventory")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "inventory_query" in prompt
    assert "inventory_low_stock" in prompt
    assert "reorder" in prompt.lower()
    assert "Never create" in prompt or "never create" in prompt.lower()


def test_get_customer_insights_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("customer_insights")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "user_query" in prompt
    assert "order_query" in prompt
    assert "get_statistics" in prompt
    assert "Never create" in prompt or "never create" in prompt.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_prompts.py::test_get_inventory_prompt_is_nonempty_and_read_only tests/test_prompts.py::test_get_customer_insights_prompt_is_nonempty_and_read_only -v`
Expected: FAIL with KeyError "not found in prompts"

- [ ] **Step 3: Add the inventory and customer_insights prompts**

Add to `src/ecommerce_agent/prompts/prompts.yml` after the `purchasing` prompt:

```yaml
inventory: |
  You are the Inventory Manager for an e-commerce operations team. You answer
  questions about stock levels, low-stock items, reorder points, and stockout
  risk using read-only inventory tools.

  You can query inventory data to check stock on hand, identify items below
  reorder point, and flag stockout risk. When stock is low, recommend that the
  operator ask Purchasing to create a reorder — but never create purchase orders
  or execute writes yourself.

  Choosing tools:
  - Use inventory_query for the stock level of specific items or SKUs.
  - Use inventory_low_stock for items below reorder point or at risk of stockout.

  Boundary:
  - Aggregate business analytics (revenue trends, sales by category, forecasts)
    are handled by the Sales Analyst specialist. If a question is about
    cross-cutting business metrics rather than stock health, say so.
  - Creating purchase orders or supplier actions are handled by the Purchasing
    specialist. Recommend reordering but do not attempt it.

  You are read-only. Never create, modify, approve, or execute writes.

customer_insights: |
  You are the Customer Insights specialist for an e-commerce operations team.
  You answer questions about customer behavior, segments, lifetime value, and
  customer-centric order history using read-only tools.

  You can query customer data, customer orders, and aggregate statistics to
  analyze customer behavior: top customers by spend, repeat vs one-time buyers,
  customer segments, and individual customer order history.

  Choosing tools:
  - Use user_query for customer profiles and customer-specific data.
  - Use order_query for a specific customer's order history.
  - Use get_statistics for aggregate customer metrics such as total customers
    or repeat-buyer counts.

  Boundary:
  - Cross-cutting business analytics (revenue trends, sales by category,
    product performance, forecasts) are handled by the Sales Analyst specialist.
  - Customer-order status changes are handled by the Order Manager specialist.
  - Stock levels and inventory questions are handled by the Inventory specialist.
  - Creating purchase orders or supplier actions are handled by the Purchasing
    specialist.

  You are read-only. Never create, modify, approve, or execute writes.
```

- [ ] **Step 4: Add agent factory functions**

Add description constants after `_ORDER_MANAGER_DESCRIPTION` in `src/ecommerce_agent/agents.py` (around line 24):

```python
_INVENTORY_DESCRIPTION = (
    "Read-only inventory manager: checks stock levels, identifies low-stock "
    "items, and recommends reordering without executing writes."
)

_CUSTOMER_INSIGHTS_DESCRIPTION = (
    "Read-only customer insights: analyzes customer behavior, segments, "
    "lifetime value, and customer order history."
)
```

Add factory functions after `build_purchasing` (around line 104):

```python
def build_inventory(
    model: BaseChatModel,
    *,
    inventory_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the read-only inventory specialist: stock health + reorder flags."""
    return build_agent(
        model,
        list(inventory_tools),
        system_prompt=get_prompt("inventory"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_customer_insights(
    model: BaseChatModel,
    *,
    customer_insights_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the read-only customer insights specialist: customer analytics."""
    return build_agent(
        model,
        list(customer_insights_tools),
        system_prompt=get_prompt("customer_insights"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )
```

- [ ] **Step 5: Run prompt tests to verify they pass**

Run: `pytest tests/test_prompts.py::test_get_inventory_prompt_is_nonempty_and_read_only tests/test_prompts.py::test_get_customer_insights_prompt_is_nonempty_and_read_only -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/ecommerce_agent/agents.py src/ecommerce_agent/prompts/prompts.yml tests/test_prompts.py
git commit -m "feat(agents): add inventory + customer-insights agent factories and prompts"
```

---

### Task 2: Specialist providers for inventory and customer-insights

**Files:**
- Modify: `src/ecommerce_agent/specialists/providers.py`
- Test: `tests/test_specialists.py`
- Test: `tests/test_routing_registry.py`
- Test: `tests/test_routing_dataset.py`

- [ ] **Step 1: Write the failing provider tests**

In `tests/test_specialists.py`, update the PROVIDERS list test and add new provider tests:

Replace `test_providers_are_sales_analyst_order_manager_and_purchasing_in_order` with:

```python
def test_providers_are_five_specialists_in_order() -> None:
    assert [p.name for p in PROVIDERS] == [
        "sales-analyst",
        "order-manager",
        "purchasing",
        "inventory",
        "customer-insights",
    ]
```

Add after `test_purchasing_tags_select_suppliers_purchase_orders_and_approval`:

```python
def test_inventory_is_read_capability() -> None:
    p = get_provider("inventory")
    assert p.capability == "read"
    assert p.prompt_key == "inventory"
    assert p.default is False
    assert p.approval_operations == frozenset()


def test_customer_insights_is_read_capability() -> None:
    p = get_provider("customer-insights")
    assert p.capability == "read"
    assert p.prompt_key == "customer_insights"
    assert p.default is False
    assert p.approval_operations == frozenset()


def test_inventory_tags_select_inventory_tools_only() -> None:
    selected = select_names(get_provider("inventory").tool_tags)
    assert selected == frozenset({"inventory_query", "inventory_low_stock"})
    assert "get_statistics" not in selected
    assert "order_query" not in selected
    assert "request_approval" not in selected


def test_customer_insights_tags_select_customer_tools_and_statistics() -> None:
    selected = select_names(get_provider("customer-insights").tool_tags)
    assert selected == frozenset({"user_query", "order_query", "get_statistics"})
    assert "inventory_query" not in selected
    assert "request_approval" not in selected
```

Also add inventory + customer-insights to `test_read_provider_is_always_enabled`:

```python
def test_read_provider_is_always_enabled() -> None:
    for name in ("sales-analyst", "inventory", "customer-insights"):
        p = get_provider(name)
        assert p.is_enabled(SimpleNamespace(can_propose=False)) is True
        assert p.is_enabled(SimpleNamespace(can_propose=True)) is True
```

In `tests/test_routing_registry.py`, update the snapshot test and names set:

```python
def test_default_specialist_is_the_flagged_one() -> None:
    reg = build_specialist_registry()
    assert reg.default.name == "sales-analyst"
    assert set(reg.names()) == {
        "sales-analyst",
        "order-manager",
        "purchasing",
        "inventory",
        "customer-insights",
    }
    assert reg.is_registered("inventory") is True
    assert reg.is_registered("customer-insights") is True
    assert reg.is_registered("unsure") is False
```

```python
def test_describe_lists_names_and_descriptions() -> None:
    reg = build_specialist_registry()
    text = reg.describe()
    assert "sales-analyst:" in text
    assert "order-manager:" in text
    assert "purchasing:" in text
    assert "inventory:" in text
    assert "customer-insights:" in text
```

```python
def test_describe_is_byte_identical_to_the_classifier_prompt_snapshot() -> None:
    reg = build_specialist_registry()
    assert reg.describe() == (
        "- sales-analyst: read-only sales analytics: querying business data, trends, "
        "forecasts, and charts.\n"
        "- order-manager: approval-only business writes: customer-order status changes "
        "(ship, cancel, update).\n"
        "- purchasing: procurement writes: create or receive purchase orders, restock, "
        "replenish, and supplier-focused proposals.\n"
        "- inventory: read-only stock health: current stock levels, low-stock items, "
        "reorder-point checks, and stockout-risk flags.\n"
        "- customer-insights: read-only customer analytics: customer behavior, segments, "
        "lifetime value, and customer order history."
    )
```

In `tests/test_routing_dataset.py`, update the expected specialists set:

```python
    assert all(
        c.expected in {
            "sales-analyst",
            "order-manager",
            "purchasing",
            "inventory",
            "customer-insights",
        }
        for c in cases
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_specialists.py tests/test_routing_registry.py tests/test_routing_dataset.py -v`
Expected: FAIL (PROVIDERS tuple only has 3 entries)

- [ ] **Step 3: Add tags, assemblers, and PROVIDERS entries**

In `src/ecommerce_agent/specialists/providers.py`:

Update the import line (around line 24):

```python
from ecommerce_agent.agents import (
    build_customer_insights,
    build_inventory,
    build_order_manager,
    build_purchasing,
    build_sales_analyst,
)
```

Add tag frozensets after `PURCHASING_TAGS` (around line 40):

```python
INVENTORY_TAGS: frozenset[str] = frozenset({"inventory.query", "inventory.low_stock"})
CUSTOMER_INSIGHTS_TAGS: frozenset[str] = frozenset(
    {"customers.query", "orders.query", "analytics.aggregate"}
)
```

Add assembler functions after `_assemble_purchasing` (around line 137):

```python
def _assemble_inventory(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_inventory(model, inventory_tools=spring_tools, backend=backend)


def _assemble_customer_insights(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_customer_insights(
        model, customer_insights_tools=spring_tools, backend=backend
    )
```

Add two entries to the `PROVIDERS` tuple, after the purchasing entry (before the closing `)`):

```python
    SpecialistProvider(
        name="inventory",
        description=(
            "read-only stock health: current stock levels, low-stock items, "
            "reorder-point checks, and stockout-risk flags."
        ),
        capability="read",
        prompt_key="inventory",
        tool_tags=INVENTORY_TAGS,
        assemble=_assemble_inventory,
    ),
    SpecialistProvider(
        name="customer-insights",
        description=(
            "read-only customer analytics: customer behavior, segments, "
            "lifetime value, and customer order history."
        ),
        capability="read",
        prompt_key="customer_insights",
        tool_tags=CUSTOMER_INSIGHTS_TAGS,
        assemble=_assemble_customer_insights,
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_specialists.py tests/test_routing_registry.py tests/test_routing_dataset.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/specialists/providers.py tests/test_specialists.py tests/test_routing_registry.py tests/test_routing_dataset.py
git commit -m "feat(specialists): add inventory + customer-insights providers"
```

---

### Task 3: Router classifier 5-way prompt

**Files:**
- Modify: `src/ecommerce_agent/prompts/prompts.yml`
- Test: `tests/test_prompts.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_prompts.py`, update `test_router_classifier_prompt_has_specialists_slot`:

```python
def test_router_classifier_prompt_has_specialists_slot() -> None:
    prompt = get_prompt("router_classifier")

    assert "{specialists}" in prompt
    assert "unsure" in prompt
    assert "purchasing" in prompt
    assert "order-manager" in prompt
    assert "inventory" in prompt
    assert "customer-insights" in prompt
    assert "stockout" in prompt
    assert "customer order history" in prompt.lower() or "customer-history" in prompt.lower()
```

- [ ] **Step 2: Run test to verify it fails (missing "inventory" / "customer-insights" / "stockout")**

Run: `pytest tests/test_prompts.py::test_router_classifier_prompt_has_specialists_slot -v`
Expected: FAIL

- [ ] **Step 3: Rewrite the router_classifier prompt**

Replace the entire `router_classifier:` entry in `src/ecommerce_agent/prompts/prompts.yml` with:

```yaml
router_classifier: |
  You route an e-commerce operator's message to exactly one specialist.

  Specialists:
  {specialists}

  Choose the single specialist whose responsibilities best fit the message.
  - Use "sales-analyst" for cross-cutting analytics, trends, forecasts, charts,
    and aggregate business reporting — revenue, sales, product performance, and
    overall business metrics.
  - Use "order-manager" for customer-order questions and actions: the status of a
    specific customer order, or shipping, canceling, or updating one.
  - Use "purchasing" for supplier and purchase-order questions and actions:
    supplier lookups, purchase-order status, creating or receiving a purchase
    order, restock, and replenish.
  - Use "inventory" for stock health: current stock levels, low-stock items,
    reorder-point checks, and stockout risk.
  - Use "customer-insights" for customer-centric analytics: top customers,
    customer segments, lifetime value, repeat vs one-time buyers, and individual
    customer order history.

  Disambiguate by subject, not by tool name:
  - A stock-level or low-stock question goes to inventory, not sales-analyst,
    even though sales-analyst can read inventory data.
  - A customer behavior or customer order history question goes to
    customer-insights, not sales-analyst or order-manager.
  - Aggregate business reporting (revenue, sales trends, product performance)
    goes to sales-analyst even when it mentions inventory or customers.
  - A question about a specific customer order's status goes to order-manager;
    a question about a customer's overall order history goes to
    customer-insights.
  - When the message asks to take an action, prefer the matching domain
    specialist over sales-analyst.

  Respond with the specialist name and a brief reason. If the message is
  genuinely ambiguous, respond with "unsure".
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_prompts.py::test_router_classifier_prompt_has_specialists_slot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/prompts/prompts.yml tests/test_prompts.py
git commit -m "feat(prompts): rewrite router_classifier for 5-way specialist routing"
```

---

### Task 4: Routing eval cases

**Files:**
- Modify: `src/ecommerce_agent/evals/datasets/routing.yaml`

- [ ] **Step 1: Re-label inventory-snapshot and add new cases**

In `src/ecommerce_agent/evals/datasets/routing.yaml`:

Change the `inventory-snapshot` case expected from `sales-analyst` to `inventory`:

```yaml
- id: inventory-snapshot
  prompt: how much inventory do we have on hand right now?
  expected: inventory
  tags: [straightforward]
```

Add new cases after the existing `supplier-lead-report` case (end of file):

```yaml
# Phase C: inventory read specialist
- id: stock-level-sku
  prompt: what's the current stock level for SKU-119?
  expected: inventory
  tags: [straightforward]
- id: low-stock-items
  prompt: which items are below their reorder point right now?
  expected: inventory
  tags: [straightforward]
- id: stockout-risk
  prompt: which products are at risk of running out of stock this week?
  expected: inventory
  tags: [straightforward]
- id: restock-recommendation
  prompt: based on current stock levels, what should we consider reordering?
  expected: inventory
  tags: [adversarial, keyword-false-negative]
# Phase C: customer-insights read specialist
- id: top-customers-spend
  prompt: who are our top customers by total spend?
  expected: customer-insights
  tags: [straightforward]
- id: repeat-vs-onetime
  prompt: how many repeat vs one-time buyers did we have last quarter?
  expected: customer-insights
  tags: [straightforward]
# Phase C: get_statistics boundary cases (per-pair subject disambiguation)
- id: inventory-turnover-trend
  prompt: chart the inventory turnover rate trend over the last 6 months
  expected: sales-analyst
  tags: [boundary, get-statistics-shared]
- id: customer-segment-ltv
  prompt: break down customers into segments and analyze their lifetime value
  expected: customer-insights
  tags: [boundary, get-statistics-shared]
- id: customer-order-history
  prompt: what's customer 88's order history?
  expected: customer-insights
  tags: [boundary]
```

- [ ] **Step 2: Verify dataset loads with updated validation**

Run: `pytest tests/test_routing_dataset.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/ecommerce_agent/evals/datasets/routing.yaml
git commit -m "test(routing): add inventory/customer-insights cases, re-label inventory-snapshot"
```

---

### Task 5: MCP client shims + /health/mcp diagnostics

**Files:**
- Modify: `src/ecommerce_agent/mcp_client.py`
- Modify: `src/ecommerce_agent/api/app.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Add shims and filter functions to mcp_client.py**

In `src/ecommerce_agent/mcp_client.py`, add after `PURCHASING_SPRING_TOOLS` (around line 31):

```python
INVENTORY_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset({"inventory.query", "inventory.low_stock"})
)
CUSTOMER_INSIGHTS_SPRING_TOOLS: frozenset[str] = select_names(
    frozenset({"customers.query", "orders.query", "analytics.aggregate"})
)
```

Add filter functions after `filter_purchasing_tools` (around line 107):

```python
def filter_inventory_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in INVENTORY_SPRING_TOOLS]


def filter_customer_insights_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in CUSTOMER_INSIGHTS_SPRING_TOOLS]
```

- [ ] **Step 2: Update /health/mcp diagnostics in api/app.py**

In `src/ecommerce_agent/api/app.py`, update the imports (around line 25-27) to add:

```python
    CUSTOMER_INSIGHTS_SPRING_TOOLS,
    INVENTORY_SPRING_TOOLS,
    filter_customer_insights_tools,
    filter_inventory_tools,
```

In `probe_mcp_server` (around line 297-315), add inventory + customer-insights diagnostics. After the `purchasing_tools` line and before the `result.update` call, add:

```python
        inventory_tools = filter_inventory_tools(tools)
        customer_insights_tools = filter_customer_insights_tools(tools)
```

Update the `result.update({...})` dict to add these keys (after the purchasing entries):

```python
                "inventory_allowed_tool_count": len(inventory_tools),
                "inventory_allowed_tools": sorted(tool_names(inventory_tools)),
                "customer_insights_allowed_tool_count": len(customer_insights_tools),
                "customer_insights_allowed_tools": sorted(
                    tool_names(customer_insights_tools)
                ),
```

And add to the missing-tools section (after `missing_expected_purchasing_tools`):

```python
                "missing_expected_inventory_tools": sorted(
                    INVENTORY_SPRING_TOOLS - names
                ),
                "missing_expected_customer_insights_tools": sorted(
                    CUSTOMER_INSIGHTS_SPRING_TOOLS - names
                ),
```

- [ ] **Step 3: Write the failing test**

In `tests/test_app.py`, update `test_mcp_health_reports_spring_tool_visibility`. After the purchasing assertions (around line 169), add:

```python
    assert spring["inventory_allowed_tool_count"] == 2
    assert spring["inventory_allowed_tools"] == ["inventory_low_stock", "inventory_query"]
    assert spring["customer_insights_allowed_tool_count"] == 3
    assert spring["customer_insights_allowed_tools"] == [
        "get_statistics",
        "order_query",
        "user_query",
    ]
    assert spring["missing_expected_inventory_tools"] == []
    assert spring["missing_expected_customer_insights_tools"] == []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_app.py::test_mcp_health_reports_spring_tool_visibility -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/mcp_client.py src/ecommerce_agent/api/app.py tests/test_app.py
git commit -m "feat(mcp_client): add inventory + customer-insights shims and health diagnostics"
```

---

### Task 6: Full verification

- [ ] **Step 1: Run the full unit test suite**

Run: `pytest -q -m "not docker and not integration and not live"`
Expected: All tests pass.

- [ ] **Step 2: Run ruff**

Run: `ruff check src/ecommerce_agent/ tests/`
Expected: No errors.

- [ ] **Step 3: Run frontend tests (no UI changes expected, but verify)**

Run: `cd frontend && npx vitest run`
Expected: All 89 tests pass.

- [ ] **Step 4: Verify routing dataset loads all 26 cases**

Run: `python -c "from ecommerce_agent.evals.routing import load_routing_cases; cases = load_routing_cases(); print(f'{len(cases)} cases'); print('specialists:', sorted({c.expected for c in cases}))"`
Expected: 26 cases, specialists include inventory and customer-insights.

- [ ] **Step 5: If all green, the implementation is complete**

The user runs the live routing eval (`RUN_LIVE_LLM=1`) and we iterate on prompt/case adjustments if accuracy drops on the get_statistics boundary cases.

---

## Notes

- **No approval-safety changes**: inventory and customer-insights are `read`-tier (never propose). The approval-safety eval only covers propose specialists.
- **No frontend changes**: the frontend renders specialist names dynamically from route-decision events. No hardcoded specialist list exists.
- **The `coordinator` prompt is dormant** (preserved seam). It is not updated for 5 specialists — it stays as-is, consistent with Phase B.
- **`sales-analyst` tool set unchanged**: it keeps the broad `spring.read` tag (all reads). The classifier prompt is what prevents stock/customer questions from routing there unnecessarily.
