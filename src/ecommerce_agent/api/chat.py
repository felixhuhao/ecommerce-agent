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

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)
STREAM_ERROR_MESSAGE = "Unable to complete the chat stream. Please try again."


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


async def _agent_sse_events(
    agent: Any,
    message: str,
    request: Request,
) -> AsyncIterator[dict[str, str]]:
    inputs = {"messages": [{"role": "user", "content": message}]}
    config = {"recursion_limit": request.app.state.settings.agent_recursion_limit}
    async for event in agent.astream_events(inputs, config=config, version="v2"):
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
        except Exception:
            logger.exception("Chat stream failed")
            yield {"event": "error", "data": _json_data({"message": STREAM_ERROR_MESSAGE})}
            yield {"event": "done", "data": _json_data({})}

    return EventSourceResponse(stream())
