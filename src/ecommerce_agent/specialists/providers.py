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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ecommerce_agent.agents import (
    build_customer_insights,
    build_inventory,
    build_order_manager,
    build_purchasing,
    build_sales_analyst,
)
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.tools.charting import (
    CREATE_CHART_SPEC_TOOL_NAME,
    build_create_chart_spec_tool,
)
from ecommerce_agent.tools.metadata import select_names
from ecommerce_agent.tools.staging import (
    STAGE_SALES_ANALYSIS_TOOL_NAME,
    build_sales_analysis_staging_tool,
)

SpecialistAssembler = Callable[..., Any]

# Tool tags selected by each specialist. ``build`` resolves these via select_names,
# so changing a set here is the only edit needed to change a specialist's tool surface.
SALES_ANALYST_TAGS: frozenset[str] = frozenset({"spring.read", "viz.chart", "analysis.staging"})
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
    {"customers.query", "orders.query", "analytics.aggregate"}
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
            selected_names=names,
            backend=backend,
        )


def _select_by_name(tools: Sequence[BaseTool], names: frozenset[str]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in names]


def _assemble_sales_analyst(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
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
    chart_tools: list[BaseTool] = []
    if CREATE_CHART_SPEC_TOOL_NAME in selected_names:
        chart_tools = [build_create_chart_spec_tool()]
    return build_sales_analyst(
        model,
        spring_read_tools=spring_tools,
        viz_tools=[*viz_tools, *chart_tools],
        staging_tools=staging,
        backend=backend,
    )


def _assemble_order_manager(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_order_manager(model, order_manager_tools=spring_tools, backend=backend)


def _assemble_purchasing(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    selected_names: frozenset[str],
    backend: Any,
) -> Any:
    return build_purchasing(model, purchasing_tools=spring_tools, backend=backend)


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
            "read-only customer analytics: customer behavior, segments, "
            "lifetime value, and customer order history."
        ),
        capability="read",
        prompt_key="customer_insights",
        tool_tags=CUSTOMER_INSIGHTS_TAGS,
        assemble=_assemble_customer_insights,
    ),
)

_BY_NAME: dict[str, SpecialistProvider] = {p.name: p for p in PROVIDERS}


def get_provider(name: str) -> SpecialistProvider:
    """Return the provider with ``name``; raise :class:`KeyError` if absent."""
    if name not in _BY_NAME:
        raise KeyError(name)
    return _BY_NAME[name]


def get_default_provider() -> SpecialistProvider:
    """Return the single default provider."""
    return next(p for p in PROVIDERS if p.default)
