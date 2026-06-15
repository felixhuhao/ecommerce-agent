"""Runtime agent factories for the analyst, order-manager, and coordinator."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ecommerce_agent.agent import build_agent
from ecommerce_agent.prompts.loader import get_prompt

_ANALYST_DESCRIPTION = (
    "Read-only sales analyst: queries business data, runs sandboxed analysis when "
    "computation is needed, and produces chart specs."
)

_ORDER_MANAGER_DESCRIPTION = (
    "Approval-only order manager: reads customer-order status, then requests "
    "human approval for proposed order-status writes (ship, cancel, update)."
)

_INVENTORY_DESCRIPTION = (
    "Read-only inventory manager: checks stock levels, identifies low-stock "
    "items, and recommends reordering without executing writes."
)

_CUSTOMER_INSIGHTS_DESCRIPTION = (
    "Read-only customer insights: analyzes customer behavior, segments, "
    "lifetime value, and customer order history."
)

_MAX_MODEL_CALLS_PER_RUN = 25
_MAX_TOOL_CALLS_PER_RUN = 40
_MONITOR_CAUSE_EXCLUDED_TOOLS = frozenset(
    {
        "write_todos",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "task",
    }
)


def _reliability_middleware() -> list[Any]:
    return [
        ModelCallLimitMiddleware(run_limit=_MAX_MODEL_CALLS_PER_RUN, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=_MAX_TOOL_CALLS_PER_RUN, exit_behavior="end"),
    ]


def build_sales_analyst(
    model: BaseChatModel,
    *,
    spring_read_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    backend: Any,
    staging_tools: Sequence[BaseTool] = (),
) -> Any:
    """Build the M1 single-specialist analyst agent on the shared sandbox backend."""
    tools = list(staging_tools) + list(spring_read_tools) + list(viz_tools)
    return build_agent(
        model,
        tools,
        system_prompt=get_prompt("sales_analyst"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_order_manager(
    model: BaseChatModel,
    *,
    order_manager_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the approval-only order manager for direct approval-intent routing."""
    return build_agent(
        model,
        list(order_manager_tools),
        system_prompt=get_prompt("order_manager"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_purchasing(
    model: BaseChatModel,
    *,
    purchasing_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the procurement specialist: supplier/PO reads + approval-only writes."""
    return build_agent(
        model,
        list(purchasing_tools),
        system_prompt=get_prompt("purchasing"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_inventory(
    model: BaseChatModel,
    *,
    inventory_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the read-only inventory specialist: stock health + reorder flags."""
    return build_agent(
        model,
        list(inventory_tools),
        system_prompt=get_prompt("inventory"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_customer_insights(
    model: BaseChatModel,
    *,
    customer_insights_tools: Sequence[BaseTool],
    backend: Any,
) -> Any:
    """Build the read-only customer insights specialist: customer analytics."""
    return build_agent(
        model,
        list(customer_insights_tools),
        system_prompt=get_prompt("customer_insights"),
        subagents=[],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )


def build_monitor_cause_agent(
    model: BaseChatModel,
    *,
    spring_read_tools: Sequence[BaseTool],
) -> Any:
    """Build the read-only cause explainer for proactive alerts."""
    return build_agent(
        model,
        list(spring_read_tools),
        system_prompt=get_prompt("monitor_cause"),
        subagents=[],
        middleware=[
            *_reliability_middleware(),
            _ToolExclusionMiddleware(excluded=_MONITOR_CAUSE_EXCLUDED_TOOLS),
        ],
        skills=[],
        backend=None,
    )


# Preserved seam: the M3 hot path routes directly to single-specialist agents.
# These descriptors and the coordinator factory stay here for the future
# multi-specialist path, where routing ambiguity can justify the extra model hop.
def sales_analyst_subagent(
    *,
    spring_read_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    staging_tools: Sequence[BaseTool] = (),
) -> dict[str, Any]:
    """Build the future coordinator's sales analyst sub-agent descriptor."""
    return {
        "name": "sales-analyst",
        "description": _ANALYST_DESCRIPTION,
        "system_prompt": get_prompt("sales_analyst"),
        "tools": list(staging_tools) + list(spring_read_tools) + list(viz_tools),
    }


def order_manager_subagent(*, order_manager_tools: Sequence[BaseTool]) -> dict[str, Any]:
    """Build the future coordinator's approval-only order manager descriptor."""
    return {
        "name": "order-manager",
        "description": _ORDER_MANAGER_DESCRIPTION,
        "system_prompt": get_prompt("order_manager"),
        "tools": list(order_manager_tools),
    }


def build_coordinator(
    model: BaseChatModel,
    *,
    sales_analyst_subagent: dict[str, Any],
    order_manager_subagent: dict[str, Any],
    backend: Any,
) -> Any:
    """Build the dormant coordinator seam for future multi-specialist routing."""
    return build_agent(
        model,
        [],
        system_prompt=get_prompt("coordinator"),
        subagents=[sales_analyst_subagent, order_manager_subagent],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )
