from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ecommerce_agent.approvals import extract_approval_id
from ecommerce_agent.mcp_client import VIZ_TOOLS
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

_SUMMARY_LIMIT = 500
_IMAGE_DATA_URI_PREFIX = "data:image/"


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


def _mime_type_from_data_uri(value: str) -> str | None:
    if not value.startswith(_IMAGE_DATA_URI_PREFIX):
        return None
    header = value.split(",", 1)[0]
    return header.removeprefix("data:").split(";", 1)[0] or None


def _image_artifact_from_output(value: Any, *, fallback_id: str | None = None) -> dict | None:
    content = getattr(value, "content", None)
    if content is not None:
        artifact = _image_artifact_from_output(content, fallback_id=fallback_id)
        if artifact:
            return artifact

    text_attr = getattr(value, "text", None)
    if isinstance(text_attr, str):
        artifact = _image_artifact_from_output(text_attr, fallback_id=fallback_id)
        if artifact:
            return artifact

    if isinstance(value, str):
        mime_type = _mime_type_from_data_uri(value)
        if mime_type:
            return {
                "id": fallback_id,
                "kind": "image",
                "mime_type": mime_type,
                "src": value,
            }
        return None

    if isinstance(value, list):
        for item in value:
            artifact = _image_artifact_from_output(item, fallback_id=fallback_id)
            if artifact:
                return artifact
        return None

    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            artifact = _image_artifact_from_output(text, fallback_id=fallback_id)
            if artifact:
                artifact_id = value.get("id") or artifact.get("id")
                if artifact_id:
                    artifact["id"] = str(artifact_id)
                return artifact
        for nested in value.values():
            artifact = _image_artifact_from_output(nested, fallback_id=fallback_id)
            if artifact:
                return artifact

    return None


def _as_token_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _token_usage_from_mapping(value: Any) -> tuple[int | None, int | None]:
    if not isinstance(value, dict):
        return None, None

    tokens_in = next(
        (
            count
            for key in ("input_tokens", "prompt_tokens", "input_token_count")
            if (count := _as_token_count(value.get(key))) is not None
        ),
        None,
    )
    tokens_out = next(
        (
            count
            for key in ("output_tokens", "completion_tokens", "output_token_count")
            if (count := _as_token_count(value.get(key))) is not None
        ),
        None,
    )
    return tokens_in, tokens_out


def _token_usage_from_output(value: Any) -> tuple[int | None, int | None]:
    candidates: list[Any] = [
        getattr(value, "usage_metadata", None),
        getattr(value, "response_metadata", None),
    ]
    if isinstance(value, dict):
        candidates.extend(
            [
                value.get("usage_metadata"),
                value.get("response_metadata"),
                value.get("llm_output"),
            ]
        )

    for candidate in candidates:
        tokens_in, tokens_out = _token_usage_from_mapping(candidate)
        if tokens_in is not None or tokens_out is not None:
            return tokens_in, tokens_out
        if isinstance(candidate, dict):
            for nested_key in ("token_usage", "usage"):
                tokens_in, tokens_out = _token_usage_from_mapping(candidate.get(nested_key))
                if tokens_in is not None or tokens_out is not None:
                    return tokens_in, tokens_out

    return None, None


def _to_trace_event(
    raw: dict,
    record: TraceRecord,
    model_chunks: dict[str, str],
) -> TraceEvent | None:
    event_type = raw.get("event")
    run_id = raw.get("run_id")
    data = raw.get("data") or {}

    if event_type == "on_chat_model_start":
        return TraceEvent(
            event_type="model_call",
            name=raw.get("name"),
            phase="start",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            args_summary=_summarize(data.get("input") or data.get("messages")),
            model_call_id=run_id,
        )

    if event_type == "on_chat_model_stream":
        text = _text_from_chunk(data.get("chunk"))
        if not text:
            return None
        model_run_id = str(run_id) if run_id is not None else "__unknown_model_run__"
        model_chunks[model_run_id] = model_chunks.get(model_run_id, "") + text
        record.answer = model_chunks[model_run_id]
        return TraceEvent(
            event_type="answer_chunk",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=text,
        )

    if event_type == "on_chat_model_end":
        output = data.get("output")
        tokens_in, tokens_out = _token_usage_from_output(output)
        return TraceEvent(
            event_type="model_call",
            name=raw.get("name"),
            phase="end",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=_summarize(output),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_call_id=run_id,
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
        output = data.get("output")
        artifact = (
            _image_artifact_from_output(output, fallback_id=str(run_id) if run_id else None)
            if raw.get("name") in VIZ_TOOLS
            else None
        )
        return TraceEvent(
            event_type="tool_call",
            name=raw.get("name"),
            phase="end",
            trace_id=record.trace_id,
            run_id=run_id,
            parent_span_id=_parent_span(raw),
            result_summary=_summarize(output),
            tool_call_id=run_id,
            artifact_id=artifact.get("id") if artifact else None,
            artifact=artifact,
            approval_id=(
                extract_approval_id(output)
                if raw.get("name") == "request_approval"
                else None
            ),
        )

    if event_type == "on_route_decision":
        info = data if isinstance(data, dict) else {}
        specialist = info.get("specialist")
        source = info.get("source")
        reason = info.get("reason", "")
        return TraceEvent(
            event_type="route_decision",
            name=specialist,
            phase="end",
            status="ok",
            trace_id=record.trace_id,
            run_id=run_id,
            result_summary=f"{source}: {reason}",
        )

    return None


async def capture(
    raw_events: AsyncIterator[dict],
    record: TraceRecord,
) -> AsyncIterator[TraceEvent]:
    """Map raw LangChain events into TraceEvents and accumulate one turn record."""
    model_chunks: dict[str, str] = {}
    span_starts: dict[tuple[str, str], TraceEvent] = {}
    async for raw in raw_events:
        event = _to_trace_event(raw, record, model_chunks)
        if event is None:
            continue
        span_key = None
        span_id = event.model_call_id or event.tool_call_id or event.run_id
        if span_id:
            span_key = (event.event_type, str(span_id))
        if event.phase == "start" and span_key:
            span_starts[span_key] = event
        elif event.phase == "end" and span_key:
            start = span_starts.get(span_key)
            if start is not None:
                event.duration_ms = (event.ts - start.ts) * 1000.0
        if event.event_type != "answer_chunk":
            record.events.append(event)
        yield event
