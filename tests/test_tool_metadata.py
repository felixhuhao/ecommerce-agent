from __future__ import annotations

from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME
from ecommerce_agent.tools.forecasting import SALES_FORECAST_TOOL_NAME
from ecommerce_agent.tools.metadata import (
    NL2SQL_TOOL_NAMES,
    TOOL_META,
    VIZ_TOOL_NAMES,
    ToolMeta,
    get_tool_meta,
    select_names,
)

# Today's authoritative sets, mirrored from mcp_client.py / trace/tools.py. The
# ToolMeta table must reproduce these exactly via tag selection so the compat
# shims and providers stay byte-compatible with the hand-maintained frozensets.
_READ_ONLY = frozenset(
    {
        "product_query",
        "product_search",
        "order_query",
        "inventory_query",
        "inventory_low_stock",
        "user_query",
        "supplier_query",
        "supplier_top",
        "purchase_order_query",
        "get_statistics",
    }
)
_ORDER_MANAGER = frozenset(
    {
        "product_query",
        "purchase_order_query",
        "order_query",
        "inventory_query",
        "supplier_query",
        "request_approval",
    }
)
_VIZ = frozenset(VIZ_TOOL_NAMES) | {CREATE_CHART_SPEC_TOOL_NAME}
_SHAPED_ANALYTICS = frozenset(
    {
        CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        SALES_BY_CATEGORY_TOOL_NAME,
        SALES_FORECAST_TOOL_NAME,
    }
)
_WRITE = frozenset(
    {"order_update", "purchase_order_create", "purchase_order_receive"}
)
_DATA_BEARING = (
    _READ_ONLY
    | NL2SQL_TOOL_NAMES
    | _SHAPED_ANALYTICS
    | {"stage_sales_analysis_inputs", "execute"}
)


def test_tool_meta_covers_every_classified_tool() -> None:
    names = {m.name for m in TOOL_META}
    expected = (
        _READ_ONLY
        | _ORDER_MANAGER
        | _VIZ
        | _SHAPED_ANALYTICS
        | _WRITE
        | NL2SQL_TOOL_NAMES
        | {"stage_sales_analysis_inputs", "execute"}
    )
    assert "request_approval" in names
    assert names == expected


def test_tool_meta_names_are_unique() -> None:
    names = [m.name for m in TOOL_META]
    assert len(names) == len(set(names))


def test_select_names_reproduces_read_only_set() -> None:
    assert select_names(frozenset({"spring.read"})) == _READ_ONLY


def test_select_names_reproduces_order_manager_set() -> None:
    tags = frozenset(
        {
            "products.query",
            "orders.query",
            "inventory.query",
            "suppliers.query",
            "purchase_orders.query",
            "approval.request",
        }
    )
    assert select_names(tags) == _ORDER_MANAGER


def test_select_names_reproduces_viz_set() -> None:
    assert select_names(frozenset({"viz.chart"})) == _VIZ


def test_select_names_reproduces_shaped_analytics_sets() -> None:
    assert select_names(frozenset({"customers.aggregate"})) == frozenset(
        {CUSTOMER_SPEND_SUMMARY_TOOL_NAME}
    )
    assert select_names(frozenset({"analytics.category"})) == frozenset(
        {SALES_BY_CATEGORY_TOOL_NAME}
    )
    assert select_names(frozenset({"analysis.forecast"})) == frozenset(
        {SALES_FORECAST_TOOL_NAME}
    )


def test_select_names_reproduces_nl2sql_set() -> None:
    assert (
        select_names(
            frozenset(
                {
                    "warehouse.schema",
                    "warehouse.query",
                    "warehouse.explain",
                    "warehouse.metric",
                }
            )
        )
        == NL2SQL_TOOL_NAMES
    )


def test_order_manager_selection_does_not_leak_unrelated_reads() -> None:
    tags = frozenset(
        {
            "products.query",
            "orders.query",
            "inventory.query",
            "suppliers.query",
            "purchase_orders.query",
            "approval.request",
        }
    )
    leaked = select_names(tags) & {
        "get_statistics",
        "user_query",
        "supplier_top",
        "inventory_low_stock",
        "product_search",
    }
    assert leaked == set()


def test_data_bearing_flags_reproduce_data_bearing_tools() -> None:
    bearing = {m.name for m in TOOL_META if m.data_bearing}
    assert bearing == _DATA_BEARING


def test_get_tool_meta_returns_none_for_unclassified_tool() -> None:
    assert get_tool_meta("write_file") is None
    assert get_tool_meta("totally_unknown") is None


def test_live_label_fields_match_today_strings() -> None:
    stats = get_tool_meta("get_statistics")
    assert stats is not None and stats.live_label_start == "Reading sales data"

    low_stock = get_tool_meta("inventory_low_stock")
    assert low_stock is not None and low_stock.live_label_start == "Reading inventory data"

    staging = get_tool_meta("stage_sales_analysis_inputs")
    assert staging is not None and staging.live_label_start == "Staging analysis inputs"

    execute = get_tool_meta("execute")
    assert execute is not None and execute.live_label_start == "Running analysis"

    viz = get_tool_meta("generate_line_chart")
    assert viz is not None
    assert viz.live_label_start == "Generating chart"
    assert viz.live_label_end == "Chart generated"

    echarts = get_tool_meta(CREATE_CHART_SPEC_TOOL_NAME)
    assert echarts is not None
    assert echarts.live_label_start == "Generating chart"
    assert echarts.live_label_end == "Chart generated"

    customers = get_tool_meta(CUSTOMER_SPEND_SUMMARY_TOOL_NAME)
    assert customers is not None
    assert customers.source == "custom"
    assert customers.live_label_start == "Reading customer spend summary"

    categories = get_tool_meta(SALES_BY_CATEGORY_TOOL_NAME)
    assert categories is not None
    assert categories.source == "custom"
    assert categories.live_label_start == "Reading category sales"

    forecast = get_tool_meta(SALES_FORECAST_TOOL_NAME)
    assert forecast is not None
    assert forecast.source == "custom"
    assert forecast.live_label_start == "Running forecast analysis"

    approval = get_tool_meta("request_approval")
    assert approval is not None
    assert approval.live_label_start == "Requesting approval"
    assert approval.live_label_end == "Approval requested"

    query = get_tool_meta("query_readonly")
    assert query is not None
    assert query.source == "nl2sql"
    assert query.live_label_start == "Querying warehouse"

    schema = get_tool_meta("list_tables")
    assert schema is not None
    assert schema.live_label_start == "Reading warehouse metadata"


def test_tool_meta_is_frozen() -> None:
    meta = ToolMeta(
        name="x",
        source="spring",
        tags=frozenset({"x.read"}),
    )
    try:
        meta.name = "y"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("ToolMeta must be frozen")
