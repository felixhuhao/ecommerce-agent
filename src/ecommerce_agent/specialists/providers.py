"""Specialist providers: declarative data + build wiring for each routed specialist.

A provider bundles everything needed to register, route to, build, and gate one
specialist. The session factory iterates :data:`PROVIDERS` instead of hand-wiring
each specialist, so adding a specialist is a single new ``SpecialistProvider`` plus
its prompt.

Tool selection is centralized in :meth:`SpecialistProvider.build`, which derives the
tool set from the provider's own ``tool_tags`` via :func:`select_names`; the
specialist-specific ``assemble`` callable then receives the already-selected tools
and owns only the assembly differences (e.g. the analyst's data-staging tool). This
keeps ``tool_tags`` the single source of truth for what a specialist receives.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ecommerce_agent.agents import (
    build_customer_insights,
    build_data_warehouse_analyst,
    build_inventory,
    build_order_manager,
    build_purchasing,
    build_sales_analyst,
)
from ecommerce_agent.config import Settings, nl2sql_configured
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
    build_customer_spend_summary_tool,
    build_sales_by_category_tool,
)
from ecommerce_agent.tools.charting import (
    CREATE_CHART_SPEC_TOOL_NAME,
    build_create_chart_spec_tool,
)
from ecommerce_agent.tools.forecasting import (
    SALES_FORECAST_TOOL_NAME,
    build_sales_forecast_tool,
)
from ecommerce_agent.tools.metadata import select_names
from ecommerce_agent.tools.staging import (
    STAGE_SALES_ANALYSIS_TOOL_NAME,
    build_sales_analysis_staging_tool,
)

SpecialistAssembler = Callable[..., Any]
logger = logging.getLogger(__name__)

# Tool tags selected by each specialist. ``build`` resolves these via select_names,
# so changing a set here is the only edit needed to change a specialist's tool surface.
SALES_ANALYST_TAGS: frozenset[str] = frozenset(
    {
        "spring.read",
        "viz.chart",
        "analysis.staging",
        "analysis.forecast",
        "analytics.category",
    }
)
ORDER_MANAGER_TAGS: frozenset[str] = frozenset({"orders.query", "approval.request"})
PURCHASING_TAGS: frozenset[str] = frozenset(
    {
        "products.search",
        "suppliers.query",
        "suppliers.top",
        "purchase_orders.query",
        "approval.request",
    }
)
INVENTORY_TAGS: frozenset[str] = frozenset(
    {"products.search", "inventory.query", "inventory.low_stock"}
)
CUSTOMER_INSIGHTS_TAGS: frozenset[str] = frozenset(
    {"analytics.aggregate", "customers.aggregate", "viz.chart"}
)
DATA_WAREHOUSE_TAGS: frozenset[str] = frozenset(
    {"warehouse.schema", "warehouse.query", "warehouse.explain", "warehouse.metric", "viz.chart"}
)

# Phase B: order-manager owns only order-status writes; PO create/receive moved to
# purchasing. purchasing owns procurement writes only (no order_status).
ORDER_MANAGER_APPROVAL_OPERATIONS: frozenset[str] = frozenset({"order_update"})
PURCHASING_APPROVAL_OPERATIONS: frozenset[str] = frozenset(
    {"purchase_order_create", "purchase_order_receive"}
)


@dataclass(frozen=True)
class SpecialistProvider:
    name: str
    description: str
    capability: Literal["read", "propose"]
    prompt_key: str
    tool_tags: frozenset[str]
    assemble: SpecialistAssembler
    approval_operations: frozenset[str] = frozenset()
    default: bool = False

    def is_enabled(self, actor: RuntimeActor) -> bool:
        return self.capability == "read" or actor.can_propose

    def build(
        self,
        *,
        model: BaseChatModel,
        spring_tools: Sequence[BaseTool],
        viz_tools: Sequence[BaseTool],
        warehouse_tools: Sequence[BaseTool] = (),
        backend: Any,
    ) -> Any:
        """Select tools by ``tool_tags`` from the loaded pools, then assemble the agent.

        Selection is the single contract: changing ``tool_tags`` is the only way to
        change what a specialist receives at runtime. ``selected_names`` is passed to
        ``assemble`` so specialist-owned custom tools (e.g. the analyst's staging tool,
        entitled by the ``analysis.staging`` tag) are built only when their tag is
        present — keeping ``tool_tags`` authoritative for custom tools too.
        """
        names = select_names(self.tool_tags)
        return self.assemble(
            model=model,
            spring_tools=_select_by_name(spring_tools, names),
            viz_tools=_select_by_name(viz_tools, names),
            warehouse_tools=_select_by_name(warehouse_tools, names),
            selected_names=names,
            backend=backend,
        )


def _select_by_name(tools: Sequence[BaseTool], names: frozenset[str]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in names]


def _optional_tool_by_name(tools: Sequence[BaseTool], name: str) -> BaseTool | None:
    for tool in tools:
        if tool.name == name:
            return tool
    return None


def _has_tools(tools: Sequence[BaseTool], names: frozenset[str]) -> bool:
    loaded = {tool.name for tool in tools}
    return names.issubset(loaded)


def _assemble_sales_analyst(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    # Custom tools are specialist-owned (not MCP-discovered), so they are built only
    # when tool_tags -> selected_names entitles them.
    staging: list[BaseTool] = []
    if STAGE_SALES_ANALYSIS_TOOL_NAME in selected_names:
        staging = [
            build_sales_analysis_staging_tool(spring_read_tools=spring_tools, backend=backend)
        ]
    forecast_tools: list[BaseTool] = []
    if SALES_FORECAST_TOOL_NAME in selected_names and _has_tools(
        spring_tools, frozenset({"order_query", "product_query"})
    ):
        forecast_tools = [
            build_sales_forecast_tool(spring_read_tools=spring_tools, backend=backend)
        ]
    elif SALES_FORECAST_TOOL_NAME in selected_names:
        logger.debug(
            "skipping %s wrapper because order_query/product_query are not loaded",
            SALES_FORECAST_TOOL_NAME,
        )
    chart_tools: list[BaseTool] = []
    if CREATE_CHART_SPEC_TOOL_NAME in selected_names:
        chart_tools = [build_create_chart_spec_tool()]
    aggregate_tools: list[BaseTool] = []
    stats_tool = _optional_tool_by_name(spring_tools, "get_statistics")
    if SALES_BY_CATEGORY_TOOL_NAME in selected_names and stats_tool is not None:
        aggregate_tools = [build_sales_by_category_tool(get_statistics=stats_tool)]
    elif SALES_BY_CATEGORY_TOOL_NAME in selected_names:
        logger.debug(
            "skipping %s wrapper because get_statistics is not loaded",
            SALES_BY_CATEGORY_TOOL_NAME,
        )
    return build_sales_analyst(
        model,
        spring_read_tools=[*spring_tools, *aggregate_tools, *forecast_tools],
        viz_tools=[*viz_tools, *chart_tools],
        staging_tools=staging,
        backend=backend,
    )


def _assemble_order_manager(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_order_manager(model, order_manager_tools=spring_tools, backend=None)


def _assemble_purchasing(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_purchasing(model, purchasing_tools=spring_tools, backend=None)


def _assemble_inventory(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_inventory(model, inventory_tools=spring_tools, backend=None)


def _assemble_customer_insights(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    chart_tools: list[BaseTool] = []
    if CREATE_CHART_SPEC_TOOL_NAME in selected_names:
        chart_tools = [build_create_chart_spec_tool()]
    aggregate_tools: list[BaseTool] = []
    stats_tool = _optional_tool_by_name(spring_tools, "get_statistics")
    if CUSTOMER_SPEND_SUMMARY_TOOL_NAME in selected_names and stats_tool is not None:
        aggregate_tools = [
            build_customer_spend_summary_tool(get_statistics=stats_tool)
        ]
    elif CUSTOMER_SPEND_SUMMARY_TOOL_NAME in selected_names:
        logger.debug(
            "skipping %s wrapper because get_statistics is not loaded",
            CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        )
    return build_customer_insights(
        model,
        customer_insights_tools=[*aggregate_tools, *chart_tools],
        backend=None,
    )


def _assemble_data_warehouse_analyst(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    warehouse_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    chart_tools: list[BaseTool] = []
    if CREATE_CHART_SPEC_TOOL_NAME in selected_names:
        chart_tools = [build_create_chart_spec_tool()]
    return build_data_warehouse_analyst(
        model,
        warehouse_tools=warehouse_tools,
        chart_tools=chart_tools,
        backend=None,
    )


PROVIDERS: tuple[SpecialistProvider, ...] = (
    SpecialistProvider(
        name="sales-analyst",
        description=(
            "read-only sales analytics: querying business data, trends, "
            "forecasts, and charts."
        ),
        capability="read",
        prompt_key="sales_analyst",
        tool_tags=SALES_ANALYST_TAGS,
        assemble=_assemble_sales_analyst,
        default=True,
    ),
    SpecialistProvider(
        name="order-manager",
        description=(
            "approval-only business writes: customer-order status changes "
            "(ship, cancel, update)."
        ),
        capability="propose",
        prompt_key="order_manager",
        tool_tags=ORDER_MANAGER_TAGS,
        assemble=_assemble_order_manager,
        approval_operations=ORDER_MANAGER_APPROVAL_OPERATIONS,
    ),
    SpecialistProvider(
        name="purchasing",
        description=(
            "procurement writes: create or receive purchase orders, restock, "
            "replenish, and supplier-focused proposals."
        ),
        capability="propose",
        prompt_key="purchasing",
        tool_tags=PURCHASING_TAGS,
        assemble=_assemble_purchasing,
        approval_operations=PURCHASING_APPROVAL_OPERATIONS,
    ),
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
            "read-only customer spend analytics: top customers, spend rankings, "
            "highest-value customers, and customer groups by spend."
        ),
        capability="read",
        prompt_key="customer_insights",
        tool_tags=CUSTOMER_INSIGHTS_TAGS,
        assemble=_assemble_customer_insights,
    ),
)

OPTIONAL_PROVIDERS: tuple[SpecialistProvider, ...] = (
    SpecialistProvider(
        name="data-warehouse-analyst",
        description=(
            "read-only warehouse analytics: ad-hoc SQL-backed historical analysis, "
            "cohorts, retention, region/channel breakdowns, long-range trends, "
            "and metric exploration. Not for current operational state or writes."
        ),
        capability="read",
        prompt_key="data_warehouse_analyst",
        tool_tags=DATA_WAREHOUSE_TAGS,
        assemble=_assemble_data_warehouse_analyst,
    ),
)

ALL_PROVIDERS: tuple[SpecialistProvider, ...] = (*PROVIDERS, *OPTIONAL_PROVIDERS)


def routeable_providers(settings: Settings | None = None) -> tuple[SpecialistProvider, ...]:
    if not nl2sql_configured(settings):
        return PROVIDERS
    return ALL_PROVIDERS

_BY_NAME: dict[str, SpecialistProvider] = {p.name: p for p in ALL_PROVIDERS}


def get_provider(name: str) -> SpecialistProvider:
    """Return the provider with ``name``; raise :class:`KeyError` if absent."""
    if name not in _BY_NAME:
        raise KeyError(name)
    return _BY_NAME[name]


def get_default_provider() -> SpecialistProvider:
    """Return the single default provider."""
    return next(p for p in PROVIDERS if p.default)
