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

from ecommerce_agent.agents import build_order_manager, build_sales_analyst
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.tools.metadata import select_names
from ecommerce_agent.tools.staging import build_sales_analysis_staging_tool

SpecialistAssembler = Callable[..., Any]

# Tool tags selected by each specialist. ``build`` resolves these via select_names,
# so changing a set here is the only edit needed to change a specialist's tool surface.
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

# Phase A: order-manager still owns every approval operation its prompt supports
# (prompts.yml). Phase B re-homes the purchase-order operations to a new
# ``purchasing`` provider and narrows this set to just ``order_update``.
ORDER_MANAGER_APPROVAL_OPERATIONS: frozenset[str] = frozenset(
    {"order_update", "purchase_order_create", "purchase_order_receive"}
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
        change what a specialist receives at runtime. ``assemble`` owns only the
        specialist-specific construction (e.g. the analyst's staging tool).
        """
        names = select_names(self.tool_tags)
        return self.assemble(
            model=model,
            spring_tools=_select_by_name(spring_tools, names),
            viz_tools=_select_by_name(viz_tools, names),
            backend=backend,
        )


def _select_by_name(tools: Sequence[BaseTool], names: frozenset[str]) -> list[BaseTool]:
    return [tool for tool in tools if tool.name in names]


def _assemble_sales_analyst(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    # ``spring_tools`` are the selected reads; build the staging tool from them so the
    # sandbox analysis has order_query/product_query to invoke.
    staging = [build_sales_analysis_staging_tool(spring_read_tools=spring_tools, backend=backend)]
    return build_sales_analyst(
        model,
        spring_read_tools=spring_tools,
        viz_tools=viz_tools,
        staging_tools=staging,
        backend=backend,
    )


def _assemble_order_manager(
    *,
    model: BaseChatModel,
    spring_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    return build_order_manager(model, order_manager_tools=spring_tools, backend=backend)


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
            "approval-only business writes: purchase orders, replenishment, "
            "receiving, and order-status changes."
        ),
        capability="propose",
        prompt_key="order_manager",
        tool_tags=ORDER_MANAGER_TAGS,
        assemble=_assemble_order_manager,
        approval_operations=ORDER_MANAGER_APPROVAL_OPERATIONS,
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
