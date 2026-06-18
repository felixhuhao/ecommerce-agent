from collections.abc import Sequence
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

_DEEPAGENTS_PROFILE_REGISTERED = False


def _register_deepagents_profile() -> None:
    """Disable DeepAgents' default general-purpose subagent for specialist graphs."""
    global _DEEPAGENTS_PROFILE_REGISTERED
    if _DEEPAGENTS_PROFILE_REGISTERED:
        return
    register_harness_profile(
        "openai",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)
        ),
    )
    _DEEPAGENTS_PROFILE_REGISTERED = True


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
    _register_deepagents_profile()
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=system_prompt,
        subagents=list(subagents),
        middleware=list(middleware),
        skills=list(skills),
        backend=backend,
    )
