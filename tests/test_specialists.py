from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.specialists.providers import (
    ALL_PROVIDERS,
    OPTIONAL_PROVIDERS,
    PROVIDERS,
    SpecialistProvider,
    get_default_provider,
    get_provider,
    routeable_providers,
)
from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME
from ecommerce_agent.tools.forecasting import SALES_FORECAST_TOOL_NAME
from ecommerce_agent.tools.metadata import NL2SQL_TOOL_NAMES, VIZ_TOOL_NAMES, select_names


def test_providers_are_five_specialists_in_order() -> None:
    assert [p.name for p in PROVIDERS] == [
        "sales-analyst",
        "order-manager",
        "purchasing",
        "inventory",
        "customer-insights",
    ]


def test_provider_names_are_unique() -> None:
    names = [p.name for p in ALL_PROVIDERS]
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
    for name in ("sales-analyst", "inventory", "customer-insights", "data-warehouse-analyst"):
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
    assert SALES_BY_CATEGORY_TOOL_NAME in selected
    assert CREATE_CHART_SPEC_TOOL_NAME in selected
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


def test_data_warehouse_provider_is_optional_and_read_only() -> None:
    assert [p.name for p in OPTIONAL_PROVIDERS] == ["data-warehouse-analyst"]
    p = get_provider("data-warehouse-analyst")
    assert p.capability == "read"
    assert p.prompt_key == "data_warehouse_analyst"
    assert p.default is False
    assert p.approval_operations == frozenset()


def test_routeable_providers_adds_warehouse_only_when_configured() -> None:
    disabled = Settings(_env_file=None, nl2sql_enabled=False, nl2sql_mcp_url="http://x")
    no_url = Settings(_env_file=None, nl2sql_enabled=True, nl2sql_mcp_url="")
    enabled = Settings(_env_file=None, nl2sql_enabled=True, nl2sql_mcp_url="http://x")

    assert [p.name for p in routeable_providers(disabled)] == [p.name for p in PROVIDERS]
    assert [p.name for p in routeable_providers(no_url)] == [p.name for p in PROVIDERS]
    assert [p.name for p in routeable_providers(enabled)] == [
        *[p.name for p in PROVIDERS],
        "data-warehouse-analyst",
    ]


def test_inventory_tags_select_product_identity_and_inventory_tools_only() -> None:
    selected = select_names(get_provider("inventory").tool_tags)
    assert selected == frozenset(
        {"product_search", "inventory_query", "inventory_low_stock"}
    )
    assert "get_statistics" not in selected
    assert "order_query" not in selected
    assert "product_query" not in selected
    assert "request_approval" not in selected


def test_customer_insights_tags_select_shaped_aggregate_and_chart_tools() -> None:
    selected = select_names(get_provider("customer-insights").tool_tags)
    assert selected == frozenset(
        {
            "get_statistics",
            CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
            CREATE_CHART_SPEC_TOOL_NAME,
        }
        | set(VIZ_TOOL_NAMES)
    )
    assert "inventory_query" not in selected
    assert "order_query" not in selected
    assert "user_query" not in selected
    assert "request_approval" not in selected


def test_data_warehouse_tags_select_only_warehouse_and_chart_tools() -> None:
    selected = select_names(get_provider("data-warehouse-analyst").tool_tags)
    assert selected == NL2SQL_TOOL_NAMES | frozenset(VIZ_TOOL_NAMES) | {CREATE_CHART_SPEC_TOOL_NAME}
    assert "get_statistics" not in selected
    assert SALES_BY_CATEGORY_TOOL_NAME not in selected
    assert CUSTOMER_SPEND_SUMMARY_TOOL_NAME not in selected
    assert "order_query" not in selected
    assert "request_approval" not in selected
    assert "execute" not in selected


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
    assert captured["warehouse_tools"] == []


def test_sales_analyst_builds_first_party_chart_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def record_analyst(model, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "AGENT"

    import ecommerce_agent.specialists.providers as providers

    monkeypatch.setattr(providers, "build_sales_analyst", record_analyst)
    get_provider("sales-analyst").build(
        model="m",
        spring_tools=[
            SimpleNamespace(name="get_statistics"),
            SimpleNamespace(name="order_query"),
            SimpleNamespace(name="product_query"),
        ],
        viz_tools=[],
        warehouse_tools=[SimpleNamespace(name="query_readonly")],
        backend="b",
    )

    assert [tool.name for tool in captured["viz_tools"]] == [CREATE_CHART_SPEC_TOOL_NAME]
    assert [tool.name for tool in captured["spring_read_tools"]] == [
        "get_statistics",
        "order_query",
        "product_query",
        SALES_BY_CATEGORY_TOOL_NAME,
        SALES_FORECAST_TOOL_NAME,
    ]


def test_customer_insights_builds_customer_spend_summary_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def record_customer_insights(model, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "AGENT"

    import ecommerce_agent.specialists.providers as providers

    monkeypatch.setattr(providers, "build_customer_insights", record_customer_insights)
    get_provider("customer-insights").build(
        model="m",
        spring_tools=[
            SimpleNamespace(name="get_statistics"),
            SimpleNamespace(name="user_query"),
            SimpleNamespace(name="order_query"),
        ],
        viz_tools=[],
        backend="b",
    )

    assert [tool.name for tool in captured["customer_insights_tools"]] == [
        CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        CREATE_CHART_SPEC_TOOL_NAME,
    ]


def test_shaped_wrapper_logs_when_backing_statistics_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured: dict = {}

    def record_analyst(model, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "AGENT"

    import ecommerce_agent.specialists.providers as providers

    monkeypatch.setattr(providers, "build_sales_analyst", record_analyst)
    caplog.set_level(logging.DEBUG, logger="ecommerce_agent.specialists.providers")

    get_provider("sales-analyst").build(
        model="m",
        spring_tools=[SimpleNamespace(name="order_query"), SimpleNamespace(name="product_query")],
        viz_tools=[],
        backend="b",
    )

    assert SALES_BY_CATEGORY_TOOL_NAME not in [
        tool.name for tool in captured["spring_read_tools"]
    ]
    assert "skipping sales_by_category wrapper" in caplog.text


def test_data_warehouse_builds_chart_tool_and_no_spring_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    def record_warehouse(model, **kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return "AGENT"

    import ecommerce_agent.specialists.providers as providers

    monkeypatch.setattr(providers, "build_data_warehouse_analyst", record_warehouse)
    get_provider("data-warehouse-analyst").build(
        model="m",
        spring_tools=[SimpleNamespace(name="get_statistics")],
        viz_tools=[],
        warehouse_tools=[
            SimpleNamespace(name="list_tables"),
            SimpleNamespace(name="query_readonly"),
            SimpleNamespace(name="execute_sql_unsafe"),
        ],
        backend="b",
    )

    assert [tool.name for tool in captured["warehouse_tools"]] == [
        "list_tables",
        "query_readonly",
    ]
    assert [tool.name for tool in captured["chart_tools"]] == [CREATE_CHART_SPEC_TOOL_NAME]
    assert captured["backend"] is None


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
