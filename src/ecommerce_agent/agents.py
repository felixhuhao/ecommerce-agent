"""Runtime agent factories for the analyst, order-manager, and coordinator."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

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
    "Approval-only order manager: reads orders, inventory, suppliers, and purchase "
    "orders, then requests human approval for proposed business writes."
)

_MAX_MODEL_CALLS_PER_RUN = 25
_MAX_TOOL_CALLS_PER_RUN = 40


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


def sales_analyst_subagent(
    *,
    spring_read_tools: Sequence[BaseTool],
    viz_tools: Sequence[BaseTool],
    staging_tools: Sequence[BaseTool] = (),
) -> dict[str, Any]:
    """Build the sales analyst sub-agent descriptor used by the coordinator."""
    return {
        "name": "sales-analyst",
        "description": _ANALYST_DESCRIPTION,
        "system_prompt": get_prompt("sales_analyst"),
        "tools": list(staging_tools) + list(spring_read_tools) + list(viz_tools),
    }


def order_manager_subagent(*, order_manager_tools: Sequence[BaseTool]) -> dict[str, Any]:
    """Build the approval-only order manager sub-agent descriptor."""
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
    """Build the M2 coordinator that routes to specialists without business tools."""
    return build_agent(
        model,
        [],
        system_prompt=get_prompt("coordinator"),
        subagents=[sales_analyst_subagent, order_manager_subagent],
        middleware=_reliability_middleware(),
        skills=[],
        backend=backend,
    )
