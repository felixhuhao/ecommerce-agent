from pathlib import Path

import pytest

from ecommerce_agent.prompts.loader import get_prompt, load_prompts


def test_get_sales_analyst_prompt_is_nonempty_and_read_only() -> None:
    prompt = get_prompt("sales_analyst")

    assert isinstance(prompt, str) and len(prompt) > 100
    assert "read-only" in prompt.lower()
    assert "ecommerce_analysis" in prompt
    assert "generate_line_chart" in prompt
    assert "generate_bar_chart" in prompt
    assert "generate_column_chart" in prompt
    assert "Never embed raw order or product payloads inside Python source" in prompt
    assert "prefer stage_sales_analysis_inputs" in prompt.lower()
    assert "Do not call order_query" in prompt
    assert "you must call exactly one chart tool" in prompt
    assert "time, value, and optional group" in prompt
    assert "do not call order_query or product_query again" in prompt
    assert "Do not include process narration" in prompt
    assert "chart" in prompt and "unavailable" in prompt
    assert "Do not call write_todos" in prompt
    assert "Do not run help()" in prompt
    assert "python3 <<'PY'" in prompt
    assert "Sales Forecast by Category" in prompt


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
    assert "Never" in prompt and "executed" in prompt
    assert "at most once" in prompt
    assert "unitCost" in prompt
    assert "Java canonicalizes unitCost" in prompt
    # Phase B: customer-order status writes stay with order-manager.
    assert "order_update" not in prompt


def test_get_coordinator_prompt_is_active_router() -> None:
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
    assert "user_query" in prompt
    assert "order_query" in prompt
    assert "get_statistics" in prompt
    assert "Never create" in prompt or "never create" in prompt.lower()


def test_router_classifier_prompt_has_specialists_slot() -> None:
    prompt = get_prompt("router_classifier")

    assert "{specialists}" in prompt
    assert "unsure" in prompt
    assert "purchasing" in prompt
    assert "order-manager" in prompt
    assert "inventory" in prompt
    assert "customer-insights" in prompt
    assert "stockout" in prompt
    assert "customer order history" in prompt.lower()


def test_get_prompt_unknown_key_raises() -> None:
    with pytest.raises(KeyError, match="not found"):
        get_prompt("does_not_exist")


def test_load_prompts_rejects_non_mapping(tmp_path: Path) -> None:
    prompts_path = tmp_path / "prompts.yml"
    prompts_path.write_text("- nope\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_prompts(str(prompts_path))
