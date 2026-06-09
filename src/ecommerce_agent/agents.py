"""M1 runtime agent factory and dormant M2 coordinator seam."""

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
) -> Any:
    """Build the M1 single-specialist analyst agent on the shared sandbox backend."""
    tools = list(spring_read_tools) + list(viz_tools)
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
) -> dict[str, Any]:
    """Dormant M2 seam for using the analyst behind a coordinator."""
    return {
        "name": "sales-analyst",
        "description": _ANALYST_DESCRIPTION,
        "system_prompt": get_prompt("sales_analyst"),
        "tools": list(spring_read_tools) + list(viz_tools),
    }
