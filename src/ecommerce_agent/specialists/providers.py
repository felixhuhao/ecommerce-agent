"""Specialist providers: declarative data + build wiring for each routed specialist.

A provider bundles everything needed to register, route to, build, and gate one
specialist. The session factory iterates :data:`PROVIDERS` instead of hand-wiring
each specialist, so adding a specialist is a single new ``SpecialistProvider`` plus
its prompt.

Each provider owns its own ``build`` callable so specialist-specific assembly (the
analyst's data-staging tool, the order-manager's approval-only tool set) lives with
the provider, not in factory control flow.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ecommerce_agent.agents import build_order_manager, build_sales_analyst
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.tools.metadata import select_names
from ecommerce_agent.tools.staging import build_sales_analysis_staging_tool

SpecialistBuild = Callable[..., Any]

# Tool tags selected by each specialist. Kept here as the authoritative per-provider
# selection set; the mcp_client compatibility frozensets reproduce today's filters.
SALES_ANALYST_TAGS: frozenset[str] = frozenset({"spring.read", "viz.chart", "analysis.staging"})
ORDER_MANAGER_TAGS: frozenset[str] = frozenset(
    {
        "products.query",
        "orders.query",
        "inventory.query",
        "suppliers.query",
        "purchase_orders.query",
        "approval.request",
    }
)


@dataclass(frozen=True)
class SpecialistProvider:
    name: str
    description: str
    capability: Literal["read", "propose"]
    prompt_key: str
    tool_tags: frozenset[str]
    build: SpecialistBuild
    approval_operations: frozenset[str] = frozenset()
    default: bool = False

    def is_enabled(self, actor: RuntimeActor) -> bool:
        return self.capability == "read" or actor.can_propose


def _build_sales_analyst(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    read_names = select_names(frozenset({"spring.read"}))
    reads = [tool for tool in spring_tools if tool.name in read_names]
    viz_names = select_names(frozenset({"viz.chart"}))
    viz = [tool for tool in viz_tools if tool.name in viz_names]
    staging = [build_sales_analysis_staging_tool(spring_read_tools=reads, backend=backend)]
    return build_sales_analyst(
        model,
        spring_read_tools=reads,
        viz_tools=viz,
        staging_tools=staging,
        backend=backend,
    )


def _build_order_manager(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    tools = [tool for tool in spring_tools if tool.name in select_names(ORDER_MANAGER_TAGS)]
    return build_order_manager(model, order_manager_tools=tools, backend=backend)


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
        build=_build_sales_analyst,
        default=True,
    ),
    SpecialistProvider(
        name="order-manager",
        description=(
            "approval-only business writes: purchase orders, replenishment, "
            "receiving, and order-status changes."
        ),
        capability="propose",
        prompt_key="order_manager",
        tool_tags=ORDER_MANAGER_TAGS,
        build=_build_order_manager,
        approval_operations=frozenset({"order_update"}),
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
