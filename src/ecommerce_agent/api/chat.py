import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, StringConstraints
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.agents import build_sales_analyst
from ecommerce_agent.mcp_client import load_modelscope_viz_tools, load_spring_read_tools
from ecommerce_agent.models import get_primary_model
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)
STREAM_ERROR_MESSAGE = "Unable to complete the chat stream. Please try again."


class ChatStreamRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _json_data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


async def _ensure_agent(request: Request) -> Any:
    if getattr(request.app.state, "agent", None) is not None:
        return request.app.state.agent

    async with request.app.state.agent_lock:
        if getattr(request.app.state, "agent", None) is not None:
            return request.app.state.agent

        settings = request.app.state.settings
        mcp_client = request.app.state.mcp_client
        spring_tools = await load_spring_read_tools(mcp_client)
        if settings.modelscope_mcp_url:
            try:
                viz_tools = await load_modelscope_viz_tools(mcp_client)
            except Exception:
                logger.warning(
                    "ModelScope MCP unavailable; continuing without visualization tools",
                    exc_info=True,
                )
                viz_tools = []
        else:
            viz_tools = []
        model = get_primary_model(settings)
        request.app.state.agent = build_sales_analyst(
            model,
            spring_read_tools=spring_tools,
            viz_tools=viz_tools,
            backend=request.app.state.sandbox_backend,
        )
        request.app.state.tool_count = len(spring_tools) + len(viz_tools)
        return request.app.state.agent


def _trace_event_to_sse(event: TraceEvent) -> dict[str, str] | None:
    if event.event_type == "answer_chunk":
        return {"event": "token", "data": _json_data({"text": event.result_summary or ""})}
    if event.event_type == "tool_call":
        return {
            "event": "tool",
            "data": _json_data({"name": event.name, "phase": event.phase}),
        }
    return None


async def _agent_sse_events(
    agent: Any,
    message: str,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    inputs = {"messages": [{"role": "user", "content": message}]}
    config = {"recursion_limit": request.app.state.settings.agent_recursion_limit}
    record = TraceRecord()
    raw_events = agent.astream_events(inputs, config=config, version="v2")
    try:
        async for event in capture(raw_events, record):
            if await request.is_disconnected():
                return

            frame = _trace_event_to_sse(event)
            if frame is not None:
                yield frame
    finally:
        if record.ended_at is None:
            record.finish()
        # M1 keeps only the most recent trace for dev/eval inspection. Concurrent
        # requests are last-writer-wins until M2 adds session/turn-indexed storage.
        request.app.state.last_trace = record


@router.post("/stream")
async def chat_stream(payload: ChatStreamRequest, request: Request) -> EventSourceResponse:
    async def stream() -> AsyncIterator[dict[str, str]]:
        try:
            agent = await _ensure_agent(request)
            async for event in _agent_sse_events(agent, payload.message, request):
                yield event
            if await request.is_disconnected():
                return
            yield {"event": "done", "data": _json_data({})}
        except Exception:
            logger.exception("Chat stream failed")
            yield {"event": "error", "data": _json_data({"message": STREAM_ERROR_MESSAGE})}
            yield {"event": "done", "data": _json_data({})}

    return EventSourceResponse(stream())
