from __future__ import annotations

from types import SimpleNamespace

import pytest

from ecommerce_agent.specialists.providers import (
    PROVIDERS,
    SpecialistProvider,
    get_default_provider,
    get_provider,
)
from ecommerce_agent.tools.metadata import select_names


def test_providers_are_five_specialists_in_order() -> None:
    assert [p.name for p in PROVIDERS] == [
        "sales-analyst",
        "order-manager",
        "purchasing",
        "inventory",
        "customer-insights",
    ]


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
    # Phase B: order-manager owns only order-status writes; PO ops moved to purchasing.
    assert p.approval_operations == frozenset({"order_update"})
    assert p.prompt_key == "order_manager"


def test_purchasing_is_propose_capability() -> None:
    p = get_provider("purchasing")
    assert p.capability == "propose"
    assert p.approval_operations == frozenset({"purchase_order_create", "purchase_order_receive"})
    assert p.prompt_key == "purchasing"
    assert p.default is False


def test_read_provider_is_always_enabled() -> None:
    for name in ("sales-analyst", "inventory", "customer-insights"):
        p = get_provider(name)
        assert p.is_enabled(SimpleNamespace(can_propose=False)) is True
        assert p.is_enabled(SimpleNamespace(can_propose=True)) is True


def test_propose_provider_is_gated_on_can_propose() -> None:
    p = get_provider("order-manager")
    assert p.is_enabled(SimpleNamespace(can_propose=False)) is False
    assert p.is_enabled(SimpleNamespace(can_propose=True)) is True
    purchasing = get_provider("purchasing")
    assert purchasing.is_enabled(SimpleNamespace(can_propose=False)) is False
    assert purchasing.is_enabled(SimpleNamespace(can_propose=True)) is True


def test_sales_analyst_tags_select_reads_viz_staging_without_writes_or_approval() -> None:
    selected = select_names(get_provider("sales-analyst").tool_tags)
    assert "get_statistics" in selected
    assert "generate_line_chart" in selected
    assert "stage_sales_analysis_inputs" in selected
    assert "order_update" not in selected
    assert "purchase_order_create" not in selected
    assert "request_approval" not in selected


def test_order_manager_tags_select_order_query_and_approval_only() -> None:
    # Phase B: order-manager narrowed to order status. It must NOT carry product/inventory/
    # supplier/PO reads (those moved to purchasing or sales-analyst).
    selected = select_names(get_provider("order-manager").tool_tags)
    assert selected == frozenset({"order_query", "request_approval"})
    assert "order_update" not in selected
    assert "get_statistics" not in selected
    assert "purchase_order_query" not in selected
    assert "product_query" not in selected


def test_purchasing_tags_select_product_identity_suppliers_purchase_orders_and_approval() -> None:
    selected = select_names(get_provider("purchasing").tool_tags)
    assert selected == frozenset(
        {
            "product_search",
            "supplier_query",
            "supplier_top",
            "purchase_order_query",
            "request_approval",
        }
    )
    assert "purchase_order_create" not in selected
    assert "purchase_order_receive" not in selected
    assert "order_query" not in selected
    assert "order_update" not in selected
    assert "product_query" not in selected


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


def test_inventory_tags_select_product_identity_and_inventory_tools_only() -> None:
    selected = select_names(get_provider("inventory").tool_tags)
    assert selected == frozenset(
        {"product_search", "inventory_query", "inventory_low_stock"}
    )
    assert "get_statistics" not in selected
    assert "order_query" not in selected
    assert "product_query" not in selected
    assert "request_approval" not in selected


def test_customer_insights_tags_select_customer_tools_and_statistics() -> None:
    selected = select_names(get_provider("customer-insights").tool_tags)
    assert selected == frozenset({"user_query", "order_query", "get_statistics"})
    assert "inventory_query" not in selected
    assert "request_approval" not in selected


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


def test_build_selects_tools_from_provider_tool_tags() -> None:
    # Locks the contract: build() derives the tool set from tool_tags, not from
    # hardcoded literals inside the assembler. Changing tool_tags must change what
    # the assembler receives.
    captured: dict = {}

    def record_assemble(**kwargs: object) -> str:
        captured.update(kwargs)
        return "AGENT"

    provider = SpecialistProvider(
        name="test",
        description="d",
        capability="read",
        prompt_key="x",
        tool_tags=frozenset({"spring.read"}),
        assemble=record_assemble,
        default=True,
    )
    spring = [
        SimpleNamespace(name="product_query"),
        SimpleNamespace(name="request_approval"),
    ]
    viz = [SimpleNamespace(name="generate_line_chart")]

    provider.build(model="m", spring_tools=spring, viz_tools=viz, backend="b")

    # Only the spring.read-tagged read is selected; request_approval and the viz tool
    # are excluded because tool_tags names only reads.
    assert [t.name for t in captured["spring_tools"]] == ["product_query"]
    assert captured["viz_tools"] == []


def test_removing_analysis_staging_tag_omits_staging_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The staging tool is specialist-owned (not MCP-discovered). It must be built only
    # when analysis.staging is in tool_tags, so tool_tags stays the single source of
    # truth for custom tools too.
    import ecommerce_agent.specialists.providers as providers
    from ecommerce_agent.tools.staging import STAGE_SALES_ANALYSIS_TOOL_NAME

    staging_calls: list[dict] = []
    monkeypatch.setattr(
        providers,
        "build_sales_analysis_staging_tool",
        lambda **kw: staging_calls.append(kw)
        or SimpleNamespace(name=STAGE_SALES_ANALYSIS_TOOL_NAME),
    )
    analyst_kwargs: dict = {}
    monkeypatch.setattr(
        providers,
        "build_sales_analyst",
        lambda model, **kw: analyst_kwargs.update(kw) or "AGENT",
    )

    reads = [SimpleNamespace(name="product_query")]
    common = {
        "name": "t",
        "description": "d",
        "capability": "read",
        "prompt_key": "x",
        "assemble": providers._assemble_sales_analyst,
        "default": True,
    }

    with_staging = SpecialistProvider(
        tool_tags=frozenset({"spring.read", "analysis.staging"}), **common
    )
    with_staging.build(model="m", spring_tools=reads, viz_tools=[], backend="b")
    assert len(staging_calls) == 1
    assert [t.name for t in analyst_kwargs["staging_tools"]] == [STAGE_SALES_ANALYSIS_TOOL_NAME]

    staging_calls.clear()
    analyst_kwargs.clear()
    without_staging = SpecialistProvider(tool_tags=frozenset({"spring.read"}), **common)
    without_staging.build(model="m", spring_tools=reads, viz_tools=[], backend="b")
    assert staging_calls == []
    assert analyst_kwargs["staging_tools"] == []
