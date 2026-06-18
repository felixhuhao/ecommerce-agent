from pathlib import Path

import pytest

from ecommerce_agent.prompts.loader import get_prompt, load_prompts


def test_get_sales_analyst_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("sales_analyst")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "ecommerce_analysis" in prompt
    assert "create_chart_spec" in prompt
    assert "sales_by_category" in prompt
    assert "bar or column" in prompt
    assert "line or area" in prompt
    assert "pie" in prompt
    assert "scatter" in prompt
    assert "Never embed raw order or product payloads inside Python source" in prompt
    assert "sales_forecast" in prompt
    assert "non-forecast sandbox analysis" in prompt
    assert "do not call get_statistics" in prompt.lower()
    assert "you must call create_chart_spec exactly once" in prompt
    assert "Do not pass raw ECharts options" in prompt
    assert "Never use line charts for category-only comparisons" in prompt
    assert "categories on the category" in prompt and "total sales on the value axis" in prompt
    assert "If sales_forecast returns" in prompt
    assert "Do not include process narration" in prompt
    assert "chart" in prompt and "unavailable" in prompt
    assert "Do not call write_todos" in prompt
    assert "Do not run help()" in prompt
    assert "python3 <<'PY'" not in prompt
    assert "forecast subject" in prompt
    assert "hero forecast" not in prompt
    assert "Customer Insights specialist" in prompt
    assert "customer groups" in prompt
    assert "Do not use sandbox analysis" in prompt


def test_get_order_manager_prompt_is_nonempty_and_order_status_only() -> None:
    prompt = get_prompt("order_manager")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "request_approval" in prompt
    assert "order_update" in prompt
    assert "order_query" in prompt
    assert "Never" in prompt and "executed" in prompt
    assert "at most once" in prompt
    assert "Do not call request_approval until" in prompt
    assert "confirm the current status with order_query" in prompt
    assert "order_query(orderId=<id>)" in prompt
    assert "Do not ask for userId when orderId lookup can verify" in prompt
    # Phase B: PO/supplier/product guidance moved to the purchasing specialist.
    assert "purchase_order_create" not in prompt
    assert "purchase_order_receive" not in prompt
    assert "product_query" not in prompt
    assert "unitCost" not in prompt


def test_get_purchasing_prompt_is_nonempty_and_procurement_approval_only() -> None:
    prompt = get_prompt("purchasing")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "request_approval" in prompt
    assert "purchase_order_create" in prompt
    assert "purchase_order_receive" in prompt
    assert "supplier_query" in prompt
    assert "supplier_top" in prompt
    assert "purchase_order_query" in prompt
    assert "product_search" in prompt
    assert "SKU" in prompt and "productId" in prompt
    assert "Never" in prompt and "executed" in prompt
    assert "at most once" in prompt
    assert "unitCost" in prompt
    assert "Java canonicalizes unitCost" in prompt
    # Phase B: customer-order status writes stay with order-manager.
    assert "order_update" not in prompt


def test_get_coordinator_prompt_is_preserved_dormant_seam() -> None:
    prompt = get_prompt("coordinator")

    assert "Dormant" not in prompt
    assert "sales-analyst" in prompt
    assert "order-manager" in prompt
    assert "no business tools" in prompt
    assert "first tool call must be the task tool" in prompt
    assert "Do not use the general-purpose subagent" in prompt
    assert "Never call" in prompt and "execute" in prompt
    assert "sales analysis" in prompt
    assert "forecasts" in prompt
    assert "charts" in prompt
    assert "purchase orders" in prompt
    assert "replenishment" in prompt
    assert "order-status changes" in prompt
    assert "approval proposal" in prompt


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
    assert "customer_spend_summary" in prompt
    assert "customer spend rankings" in prompt
    assert "customer groups by spend" in prompt
    assert "highest-value" in prompt and "customer questions" in prompt
    assert "topCustomersBySpend" not in prompt
    assert "order history to compute top-level spend aggregates" in prompt
    assert "tool" in prompt and "results" in prompt
    assert "create_chart_spec exactly once" in prompt
    assert "Do not include process narration" in prompt
    assert '"Let me"' in prompt
    assert "Never create" in prompt or "never create" in prompt.lower()


def test_get_data_warehouse_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("data_warehouse_analyst")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "query_readonly" in prompt
    assert "create_chart_spec" in prompt
    assert "Prefer one" in prompt and "query_readonly" in prompt
    assert "answer from that" in prompt
    assert "follow-up validation" in prompt
    assert "query_readonly at most four times" in prompt
    assert "get_table_schema at most five" in prompt
    assert "do not query" in prompt.lower() and "information_schema" in prompt
    assert "DuckDB-compatible" in prompt
    assert "strftime" in prompt and "format" in prompt
    assert "min/max date probes" in prompt
    assert "fact_orders.payment_amount" in prompt
    assert "fact_order_items is not needed" in prompt
    assert "exactly once" in prompt
    assert "warehouse" in prompt.lower()
    assert "current stock" in prompt.lower()
    assert "Do not silently merge" in prompt


def test_router_classifier_prompt_has_specialists_slot() -> None:
    prompt = get_prompt("router_classifier")

    assert "{specialists}" in prompt
    assert "unsure" in prompt
    assert "purchasing" in prompt
    assert "order-manager" in prompt
    assert "inventory" in prompt
    assert "customer-insights" in prompt
    assert "data-warehouse-analyst" in prompt
    assert "stockout" in prompt
    assert "overall order history goes to sales-analyst" in prompt
    assert "customer groups by spend" in prompt
    assert "repeat-buyer" in prompt and "sales-analyst" in prompt
    assert "unless the user explicitly asks" in prompt
    assert "If \"data-warehouse-analyst\" is listed" in prompt


def test_get_prompt_unknown_key_raises() -> None:
    with pytest.raises(KeyError, match="not found"):
        get_prompt("does_not_exist")


def test_load_prompts_rejects_non_mapping(tmp_path: Path) -> None:
    prompts_path = tmp_path / "prompts.yml"
    prompts_path.write_text("- nope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_prompts(str(prompts_path))
