"""Single source of truth for tool classification.

Replaces the scattered name-keyed frozensets (`READ_ONLY_SPRING_TOOLS`,
`ORDER_MANAGER_SPRING_TOOLS`, `WRITE_SPRING_TOOLS`, `APPROVAL_SPRING_TOOLS`,
`VIZ_TOOLS`, `DATA_BEARING_TOOLS`) and the slice-8 `_tool_label` string literals
with one declarative table. Consumers select tools by tag intersection and read
classification flags from here.

Tag conventions:
- Spring read tools carry the coarse `spring.read` tag (selected only by
  specialists that own the full read surface) plus a fine domain tag
  (`orders.query`, `products.search`, ...) used by specialists that own a slice.
- Direct write tools carry operation tags (`orders.update`, ...) and are never
  selected for agents; they are classified here because diagnostics/evals and the
  "one table answers what kind of tool is this" acceptance require it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME


@dataclass(frozen=True)
class ToolMeta:
    name: str
    source: Literal["spring", "modelscope", "custom", "backend", "nl2sql"]
    tags: frozenset[str]
    data_bearing: bool = False
    live_label_start: str | None = None
    live_label_end: str | None = None


def _spring_read(name: str, tag: str, **extra: object) -> ToolMeta:
    return ToolMeta(
        name, "spring", frozenset({"spring.read", tag}), data_bearing=True, **extra
    )


def _viz(name: str) -> ToolMeta:
    return ToolMeta(
        name,
        "modelscope",
        frozenset({"viz.chart"}),
        live_label_start="Generating chart",
        live_label_end="Chart generated",
    )


VIZ_TOOL_NAMES: tuple[str, ...] = (
    "generate_area_chart",
    "generate_bar_chart",
    "generate_boxplot_chart",
    "generate_column_chart",
    "generate_district_map",
    "generate_dual_axes_chart",
    "generate_fishbone_diagram",
    "generate_flow_diagram",
    "generate_funnel_chart",
    "generate_histogram_chart",
    "generate_line_chart",
    "generate_liquid_chart",
    "generate_mind_map",
    "generate_network_graph",
    "generate_organization_chart",
    "generate_path_map",
    "generate_pie_chart",
    "generate_pin_map",
    "generate_radar_chart",
    "generate_sankey_chart",
    "generate_scatter_chart",
    "generate_spreadsheet",
    "generate_treemap_chart",
    "generate_venn_chart",
    "generate_violin_chart",
    "generate_waterfall_chart",
    "generate_word_cloud_chart",
)

# These names are exact NL2SQL MCP tool names discovered from the external project.
# Tool classification is currently global by tool name, so a future server exposing
# the same generic names would need namespacing or a source-aware metadata lookup.
NL2SQL_SCHEMA_TOOLS: frozenset[str] = frozenset({"list_tables", "get_table_schema"})
NL2SQL_QUERY_TOOL = "query_readonly"
NL2SQL_EXPLAIN_TOOL = "explain_query"
NL2SQL_METRIC_TOOL = "metric_catalog_search"
NL2SQL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        *NL2SQL_SCHEMA_TOOLS,
        NL2SQL_QUERY_TOOL,
        NL2SQL_EXPLAIN_TOOL,
        NL2SQL_METRIC_TOOL,
    }
)


TOOL_META: tuple[ToolMeta, ...] = (
    # --- Spring read tools ---
    _spring_read("product_query", "products.query"),
    _spring_read("product_search", "products.search"),
    _spring_read("order_query", "orders.query"),
    _spring_read("inventory_query", "inventory.query"),
    _spring_read(
        "inventory_low_stock", "inventory.low_stock", live_label_start="Reading inventory data"
    ),
    _spring_read("user_query", "customers.query"),
    _spring_read("supplier_query", "suppliers.query"),
    _spring_read("supplier_top", "suppliers.top"),
    _spring_read("purchase_order_query", "purchase_orders.query"),
    _spring_read("get_statistics", "analytics.aggregate", live_label_start="Reading sales data"),
    # --- Spring approval (propose-only, not data-bearing) ---
    ToolMeta(
        "request_approval",
        "spring",
        frozenset({"approval.request"}),
        live_label_start="Requesting approval",
        live_label_end="Approval requested",
    ),
    # --- Spring direct write tools (never selected for agents) ---
    ToolMeta("order_update", "spring", frozenset({"spring.write", "orders.update"})),
    ToolMeta(
        "purchase_order_create", "spring", frozenset({"spring.write", "purchase_orders.create"})
    ),
    ToolMeta(
        "purchase_order_receive", "spring", frozenset({"spring.write", "purchase_orders.receive"})
    ),
    # --- ModelScope viz tools ---
    *(_viz(name) for name in VIZ_TOOL_NAMES),
    # --- First-party chart spec tool ---
    ToolMeta(
        CREATE_CHART_SPEC_TOOL_NAME,
        "custom",
        frozenset({"viz.chart"}),
        live_label_start="Generating chart",
        live_label_end="Chart generated",
    ),
    # --- First-party shaped analytics wrappers over Spring aggregate statistics ---
    ToolMeta(
        CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        "custom",
        frozenset({"customers.aggregate"}),
        data_bearing=True,
        live_label_start="Reading customer spend summary",
    ),
    ToolMeta(
        SALES_BY_CATEGORY_TOOL_NAME,
        "custom",
        frozenset({"analytics.category"}),
        data_bearing=True,
        live_label_start="Reading category sales",
    ),
    # --- Custom staging tool ---
    ToolMeta(
        "stage_sales_analysis_inputs",
        "custom",
        frozenset({"analysis.staging"}),
        data_bearing=True,
        live_label_start="Staging analysis inputs",
    ),
    # --- Backend-injected sandbox execution ---
    ToolMeta(
        "execute",
        "backend",
        frozenset({"backend.execute"}),
        data_bearing=True,
        live_label_start="Running analysis",
    ),
    # --- Optional NL2SQL analytical warehouse MCP tools ---
    ToolMeta(
        "list_tables",
        "nl2sql",
        frozenset({"warehouse.schema"}),
        data_bearing=True,
        live_label_start="Reading warehouse metadata",
    ),
    ToolMeta(
        "get_table_schema",
        "nl2sql",
        frozenset({"warehouse.schema"}),
        data_bearing=True,
        live_label_start="Reading warehouse metadata",
    ),
    ToolMeta(
        NL2SQL_QUERY_TOOL,
        "nl2sql",
        frozenset({"warehouse.query"}),
        data_bearing=True,
        live_label_start="Querying warehouse",
    ),
    ToolMeta(
        NL2SQL_EXPLAIN_TOOL,
        "nl2sql",
        frozenset({"warehouse.explain"}),
        data_bearing=True,
        live_label_start="Explaining warehouse query",
    ),
    ToolMeta(
        NL2SQL_METRIC_TOOL,
        "nl2sql",
        frozenset({"warehouse.metric"}),
        data_bearing=True,
        live_label_start="Reading warehouse metadata",
    ),
)

_BY_NAME: dict[str, ToolMeta] = {meta.name: meta for meta in TOOL_META}


def get_tool_meta(name: str | None) -> ToolMeta | None:
    """Return metadata for a tool name, or ``None`` if it is not classified."""
    if name is None:
        return None
    return _BY_NAME.get(name)


def select_names(wanted_tags: frozenset[str]) -> frozenset[str]:
    """Names of every classified tool whose tags intersect ``wanted_tags``."""
    return frozenset(meta.name for meta in TOOL_META if meta.tags & wanted_tags)
