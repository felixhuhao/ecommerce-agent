from __future__ import annotations

from ecommerce_agent.grounding.model import Authority
from ecommerce_agent.monitoring.models import AlertGrounding, AlertSource, Finding
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import is_data_bearing

CANONICAL_DETECTION_TOOLS = frozenset({"get_statistics", "inventory_low_stock"})


def build_alert_grounding(
    finding: Finding,
    *,
    cause_record: TraceRecord | None = None,
    diagnostic: str | None = None,
) -> AlertGrounding:
    sources = [
        AlertSource(
            source_id=evidence.source_id,
            tool_name=evidence.tool_name,
            args_summary=evidence.args_summary,
            result_summary=evidence.result_summary,
            evidence=evidence.evidence,
        )
        for evidence in finding.evidence
    ]
    if cause_record is not None:
        sources.extend(_cause_sources(cause_record))

    authority = (
        Authority.AUTHORITATIVE
        if any(source.tool_name in CANONICAL_DETECTION_TOOLS for source in sources)
        else Authority.UNVERIFIED
    )
    return AlertGrounding(authority=authority, sources=sources, diagnostic=diagnostic)


def _cause_sources(record: TraceRecord) -> list[AlertSource]:
    sources: list[AlertSource] = []
    for event in record.events:
        if event.event_type != "tool_call" or event.phase != "end":
            continue
        if not is_data_bearing(event.name):
            continue
        sources.append(
            AlertSource(
                source_id=f"cause:{event.tool_call_id or event.span_id}",
                tool_name=event.name or "unknown",
                args_summary=event.args_summary,
                result_summary=event.result_summary,
                evidence=event.evidence,
            )
        )
    return sources

