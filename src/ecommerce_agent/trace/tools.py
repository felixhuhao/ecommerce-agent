from __future__ import annotations

from ecommerce_agent.mcp_client import READ_ONLY_SPRING_TOOLS
from ecommerce_agent.tools.staging import STAGE_SALES_ANALYSIS_TOOL_NAME
from ecommerce_agent.trace.schema import TraceRecord

GET_STATISTICS_TOOL = "get_statistics"
EXECUTE_TOOL = "execute"

# Tool calls whose output is evidence for an analytical claim. Explicit allowlist:
# DeepAgents filesystem/scaffolding tools, viz tools, and request_approval are excluded.
DATA_BEARING_TOOLS: frozenset[str] = (
    READ_ONLY_SPRING_TOOLS | {STAGE_SALES_ANALYSIS_TOOL_NAME, EXECUTE_TOOL}
)


def fired_tools(record: TraceRecord) -> list[str]:
    """Tool names from tool_call start events, deduped in first-seen order."""
    names: list[str] = []
    for event in record.events:
        if event.event_type != "tool_call" or event.phase != "start" or not event.name:
            continue
        if event.name not in names:
            names.append(event.name)
    return names


def is_data_bearing(tool_name: str | None) -> bool:
    return tool_name in DATA_BEARING_TOOLS


def sandbox_evidence_fired(record: TraceRecord) -> bool:
    """True if a sandbox code-execution (`execute`) span completed with output."""
    return any(
        event.event_type == "tool_call"
        and event.phase == "end"
        and event.name == EXECUTE_TOOL
        and event.status == "ok"
        and bool(getattr(event, "evidence", None) or event.result_summary)
        for event in record.events
    )
