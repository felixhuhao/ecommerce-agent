from __future__ import annotations

import re

from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import (
    GET_STATISTICS_TOOL,
    fired_tools,
    is_data_bearing,
    sandbox_evidence_fired,
)

AUTHORITATIVE_READ_TOOLS = frozenset(
    {
        GET_STATISTICS_TOOL,
        "inventory_query",
        "inventory_low_stock",
    }
)

_NUMERIC_CLAIM = re.compile(
    r"\$\s?\d|\d\s?%|\b\d[\d,]*\.\d+\b|\b\d{1,3}(?:,\d{3})+\b|\b\d{2,}\b"
)


def has_numeric_claim(answer: str) -> bool:
    return bool(_NUMERIC_CLAIM.search(answer or ""))


def _source_span_id(event) -> str:
    return event.tool_call_id or event.span_id


def _sources(record: TraceRecord) -> list[GroundingSource]:
    sources: list[GroundingSource] = []
    for event in record.events:
        if event.event_type != "tool_call" or event.phase != "end":
            continue
        if not is_data_bearing(event.name):
            continue
        sources.append(
            GroundingSource(
                span_id=_source_span_id(event),
                tool_name=event.name,
                args_summary=event.args_summary,
                result_summary=event.result_summary,
            )
        )
    return sources


def build_grounding(record: TraceRecord) -> Grounding:
    """Project a turn's trace into deterministic grounding metadata."""
    try:
        fired = fired_tools(record)
        sources = _sources(record)
        numeric = has_numeric_claim(record.answer)
        if AUTHORITATIVE_READ_TOOLS.intersection(fired):
            authority = Authority.AUTHORITATIVE
        elif sandbox_evidence_fired(record):
            authority = Authority.DERIVED
        elif numeric:
            authority = Authority.UNVERIFIED
        else:
            authority = Authority.NOT_APPLICABLE
        return Grounding(authority=authority, sources=sources)
    except Exception:
        if has_numeric_claim(getattr(record, "answer", "")):
            return Grounding(authority=Authority.UNVERIFIED, diagnostic="grounding_error")
        return Grounding(authority=Authority.NOT_APPLICABLE, diagnostic="grounding_error")
