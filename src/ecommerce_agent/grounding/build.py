from __future__ import annotations

import re

from ecommerce_agent.grounding.model import Authority, Grounding, GroundingSource
from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.forecasting import SALES_FORECAST_TOOL_NAME
from ecommerce_agent.tools.metadata import NL2SQL_QUERY_TOOL
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.tools import (
    GET_STATISTICS_TOOL,
    is_data_bearing,
    sandbox_evidence_fired,
)

AUTHORITATIVE_READ_TOOLS = frozenset(
    {
        GET_STATISTICS_TOOL,
        CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
        SALES_BY_CATEGORY_TOOL_NAME,
        "inventory_query",
        "inventory_low_stock",
        NL2SQL_QUERY_TOOL,
    }
)
DERIVED_READ_TOOLS = frozenset({SALES_FORECAST_TOOL_NAME})

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
        if event.status != "ok":
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


def _successful_tool_ends(record: TraceRecord) -> set[str]:
    return {
        event.name
        for event in record.events
        if event.event_type == "tool_call"
        and event.phase == "end"
        and event.status == "ok"
        and event.name
    }


def build_grounding(record: TraceRecord) -> Grounding:
    """Project a turn's trace into deterministic grounding metadata."""
    try:
        completed = _successful_tool_ends(record)
        sources = _sources(record)
        numeric = has_numeric_claim(record.answer)
        if AUTHORITATIVE_READ_TOOLS.intersection(completed):
            authority = Authority.AUTHORITATIVE
        elif DERIVED_READ_TOOLS.intersection(completed):
            authority = Authority.DERIVED
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
