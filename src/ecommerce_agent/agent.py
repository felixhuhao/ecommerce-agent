from collections.abc import Sequence
from typing import Any

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool


def build_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    *,
    system_prompt: str,
    subagents: Sequence[Any] = (),
    middleware: Sequence[Any] = (),
    skills: Sequence[str] = (),
    backend: Any | None = None,
) -> Any:
    """Build a DeepAgents graph while threading the future extension slots."""
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        subagents=list(subagents),
        middleware=list(middleware),
        skills=list(skills),
        backend=backend,
    )
