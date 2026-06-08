from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

SYSTEM_PROMPT = """You are the E-commerce Operations AI Assistant.

Week 1 scope:
- Answer e-commerce operations questions using the available read-only business tools.
- Use SpringBoot MCP tools for products, orders, inventory, users, suppliers,
  purchase orders, and statistics.
- Do not attempt writes, approvals, order changes, or purchase-order creation.
- Be concise, cite concrete tool data when available, and ask for clarification only when required.
"""


def build_agent(model: BaseChatModel, tools: Sequence[BaseTool]) -> Any:
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=SYSTEM_PROMPT,
    )
