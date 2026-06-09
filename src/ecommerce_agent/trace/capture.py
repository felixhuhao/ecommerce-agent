from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ecommerce_agent.approvals import extract_approval_id
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

_SUMMARY_LIMIT = 500


def _summarize(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else repr(value)
    suffix = "..." if len(text) > _SUMMARY_LIMIT else ""
    return f"{text[:_SUMMARY_LIMIT]}{suffix}"


def _text_from_chunk(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _parent_span(raw: dict) -> str | None:
    # LangChain v2 events do not always include parent_ids. M1 keeps the trace
    # useful even when the span tree is shallow; OTel export can rebuild richer
    # parentage from framework-specific fields later.
    parents = raw.get("parent_ids") or []
    return parents[-1] if parents else None


def _to_trace_event(raw: dict, record: TraceRecord) -> TraceEvent | None:
    event_type = raw.get("event")
    run_id = raw.get("run_id")
    data = raw.get("data") or {}

    if event_type == "on_chat_model_stream":
        text = _text_from_chunk(data.get("chunk"))
        if not text:
            return None
        record.answer += text
        return TraceEvent(
            event_type="answer_chunk",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=text,
        )

    if event_type == "on_tool_start":
        return TraceEvent(
            event_type="tool_call",
            name=raw.get("name"),
            phase="start",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            args_summary=_summarize(data.get("input")),
            tool_call_id=run_id,
        )

    if event_type == "on_tool_end":
        return TraceEvent(
            event_type="tool_call",
            name=raw.get("name"),
            phase="end",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=_summarize(data.get("output")),
            tool_call_id=run_id,
            approval_id=(
                extract_approval_id(data.get("output"))
                if raw.get("name") == "request_approval"
                else None
            ),
        )

    return None


async def capture(
    raw_events: AsyncIterator[dict],
    record: TraceRecord,
) -> AsyncIterator[TraceEvent]:
    """Map raw LangChain events into TraceEvents and accumulate one turn record."""
    async for raw in raw_events:
        event = _to_trace_event(raw, record)
        if event is None:
            continue
        if event.event_type != "answer_chunk":
            record.events.append(event)
        yield event
