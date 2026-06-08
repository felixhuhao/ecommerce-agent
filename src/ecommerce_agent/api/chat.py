import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, StringConstraints
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.agent import build_agent
from ecommerce_agent.mcp_client import load_spring_read_tools
from ecommerce_agent.models import get_primary_model

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatStreamRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _json_data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


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


async def _ensure_agent(request: Request) -> Any:
    if getattr(request.app.state, "agent", None) is not None:
        return request.app.state.agent

    async with request.app.state.agent_lock:
        if getattr(request.app.state, "agent", None) is not None:
            return request.app.state.agent

        settings = request.app.state.settings
        tools = await load_spring_read_tools(request.app.state.mcp_client)
        model = get_primary_model(settings)
        request.app.state.agent = build_agent(model, tools)
        request.app.state.tool_count = len(tools)
        return request.app.state.agent


async def _agent_sse_events(
    agent: Any,
    message: str,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    inputs = {"messages": [{"role": "user", "content": message}]}
    async for event in agent.astream_events(inputs, version="v2"):
        if await request.is_disconnected():
            return

        event_type = event.get("event")

        if event_type == "on_chat_model_stream":
            text = _text_from_chunk(event.get("data", {}).get("chunk"))
            if text:
                yield {"event": "token", "data": _json_data({"text": text})}
            continue

        if event_type == "on_tool_start":
            yield {
                "event": "tool",
                "data": _json_data({"name": event.get("name"), "phase": "start"}),
            }
            continue

        if event_type == "on_tool_end":
            yield {
                "event": "tool",
                "data": _json_data({"name": event.get("name"), "phase": "end"}),
            }


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
        except Exception as exc:
            yield {"event": "error", "data": _json_data({"message": str(exc)})}
            yield {"event": "done", "data": _json_data({})}

    return EventSourceResponse(stream())
