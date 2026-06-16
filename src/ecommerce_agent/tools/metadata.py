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


@dataclass(frozen=True)
class ToolMeta:
    name: str
    source: Literal["spring", "modelscope", "custom", "backend"]
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
