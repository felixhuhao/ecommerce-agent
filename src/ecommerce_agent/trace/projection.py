from __future__ import annotations

from typing import Any

from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

_SPAN_EVENT_TYPES = {"model_call", "tool_call", "route_decision"}


def _new_span(event: TraceEvent, span_id: str) -> dict[str, Any]:
    return {
        "kind": event.event_type,
        "name": event.name,
        "status": event.status,
        "ts": event.ts,
        "duration_ms": event.duration_ms,
        "args_summary": None,
        "result_summary": None,
        "tokens_in": None,
        "tokens_out": None,
        "span_id": span_id,
        "artifact_id": None,
        "approval_id": None,
        "error_message": None,
    }


def _merge(span: dict[str, Any], event: TraceEvent) -> None:
    if event.phase == "start":
        span["ts"] = event.ts
        span["args_summary"] = event.args_summary or span["args_summary"]
    elif event.phase == "end":
        span["status"] = event.status
        span["duration_ms"] = event.duration_ms
        span["result_summary"] = event.result_summary or span["result_summary"]
        span["tokens_in"] = event.tokens_in
        span["tokens_out"] = event.tokens_out
        span["error_message"] = event.error_message
    span["name"] = span["name"] or event.name
    if event.artifact_id:
        span["artifact_id"] = event.artifact_id
    if event.approval_id:
        span["approval_id"] = event.approval_id


def project_timeline(record: TraceRecord) -> dict[str, Any]:
    """Project a TraceRecord into an ordered, UI-friendly span timeline."""
    spans: dict[str, dict[str, Any]] = {}
    for event in record.events:
        if event.event_type not in _SPAN_EVENT_TYPES:
            continue
        span_id = event.tool_call_id or event.model_call_id or event.span_id
        span = spans.get(span_id)
        if span is None:
            span = _new_span(event, span_id)
            spans[span_id] = span
        _merge(span, event)

    ordered = sorted(spans.values(), key=lambda span: span["ts"])

    def _total(field: str) -> int | None:
        values = [span[field] for span in ordered if span[field] is not None]
        return sum(values) if values else None

    return {
        "trace_id": record.trace_id,
        "session_id": record.session_id,
        "turn_id": record.turn_id,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "duration_ms": record.duration_ms,
        "tokens_in_total": _total("tokens_in"),
        "tokens_out_total": _total("tokens_out"),
        "span_count": len(ordered),
        "spans": ordered,
    }
