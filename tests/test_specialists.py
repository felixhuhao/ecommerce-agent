from __future__ import annotations

from types import SimpleNamespace

import pytest

from ecommerce_agent.specialists.providers import (
    PROVIDERS,
    get_default_provider,
    get_provider,
)
from ecommerce_agent.tools.metadata import select_names


def test_providers_are_exactly_sales_analyst_and_order_manager_in_order() -> None:
    assert [p.name for p in PROVIDERS] == ["sales-analyst", "order-manager"]


def test_provider_names_are_unique() -> None:
    names = [p.name for p in PROVIDERS]
    assert len(names) == len(set(names))


def test_exactly_one_default_and_it_is_sales_analyst() -> None:
    defaults = [p for p in PROVIDERS if p.default]
    assert len(defaults) == 1
    assert defaults[0].name == "sales-analyst"
    assert get_default_provider().name == "sales-analyst"


def test_sales_analyst_is_read_capability() -> None:
    p = get_provider("sales-analyst")
    assert p.capability == "read"
    assert p.default is True
    assert p.prompt_key == "sales_analyst"


def test_order_manager_is_propose_capability() -> None:
    p = get_provider("order-manager")
    assert p.capability == "propose"
    assert p.approval_operations == frozenset({"order_update"})
    assert p.prompt_key == "order_manager"


def test_read_provider_is_always_enabled() -> None:
    p = get_provider("sales-analyst")
    assert p.is_enabled(SimpleNamespace(can_propose=False)) is True
    assert p.is_enabled(SimpleNamespace(can_propose=True)) is True


def test_propose_provider_is_gated_on_can_propose() -> None:
    p = get_provider("order-manager")
    assert p.is_enabled(SimpleNamespace(can_propose=False)) is False
    assert p.is_enabled(SimpleNamespace(can_propose=True)) is True


def test_sales_analyst_tags_select_reads_viz_staging_without_writes_or_approval() -> None:
    selected = select_names(get_provider("sales-analyst").tool_tags)
    assert "get_statistics" in selected
    assert "generate_line_chart" in selected
    assert "stage_sales_analysis_inputs" in selected
    assert "order_update" not in selected
    assert "purchase_order_create" not in selected
    assert "request_approval" not in selected


def test_order_manager_tags_select_its_reads_and_approval_only() -> None:
    selected = select_names(get_provider("order-manager").tool_tags)
    assert selected == frozenset(
        {
            "product_query",
            "purchase_order_query",
            "order_query",
            "inventory_query",
            "supplier_query",
            "request_approval",
        }
    )
    assert "order_update" not in selected
    assert "get_statistics" not in selected


def test_get_provider_raises_for_unknown_name() -> None:
    with pytest.raises(KeyError):
        get_provider("nope")


def test_provider_is_frozen() -> None:
    p = get_provider("sales-analyst")
    try:
        p.name = "x"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("SpecialistProvider must be frozen")
