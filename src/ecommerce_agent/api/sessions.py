from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StringConstraints
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import append_and_publish

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class MessageRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> dict[str, str]:
    session_id = await request.app.state.session_registry.create()
    return {"session_id": session_id}


@router.get("/{session_id}/thread")
async def get_thread(session_id: str, request: Request) -> dict[str, Any]:
    messages = await request.app.state.thread_store.list_messages(session_id)
    return {"session_id": session_id, "messages": [message.model_dump() for message in messages]}


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
) -> dict[str, Any]:
    registry = request.app.state.session_registry
    try:
        runtime = await registry.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc

    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    settings = request.app.state.settings
    turn_id = uuid.uuid4().hex

    user_message = await append_and_publish(
        store,
        bus,
        ThreadMessage(
            session_id=session_id,
            type="user",
            content=payload.message,
            turn_id=turn_id,
            actor_id="operator",
        ),
    )

    app_state = request.app.state

    async def run_and_record_trace() -> None:
        record = await run_turn(
            agent=runtime.agent,
            message=payload.message,
            session_id=session_id,
            turn_id=turn_id,
            store=store,
            bus=bus,
            recursion_limit=settings.agent_recursion_limit,
        )
        trace_records = app_state.trace_records
        trace_records.setdefault(session_id, {})[turn_id] = record
        # Compatibility shortcut for the sequential live reliability harness.
        app_state.last_trace = record

    task = asyncio.create_task(run_and_record_trace())
    background_tasks = request.app.state.background_tasks
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return {"turn_id": turn_id, "user_message_id": user_message.message_id}


@router.get("/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    return EventSourceResponse(_session_events(session_id, request, store, bus))


async def _session_events(
    session_id: str,
    request: Request,
    store: Any,
    bus: Any,
) -> AsyncIterator[dict[str, str]]:
    async with bus.subscription(session_id) as sub:
        backlog = await store.list_messages(session_id)
        cursor = backlog[-1].seq if backlog else 0
        for message in backlog:
            yield {
                "event": "thread.append",
                "data": _data({"message": message.model_dump()}),
            }
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if event["event"] == "thread.append" and event["message"]["seq"] <= cursor:
                continue
            yield {"event": event["event"], "data": _data(event)}
