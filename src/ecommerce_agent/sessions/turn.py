from __future__ import annotations

import logging
from typing import Any

from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import ThreadStore, append_and_publish
from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord

logger = logging.getLogger(__name__)


def _trace_event_to_frame(event: TraceEvent) -> dict | None:
    if event.event_type == "answer_chunk":
        return {"event": "token", "text": event.result_summary or ""}
    if event.event_type == "tool_call":
        return {"event": "tool", "name": event.name, "phase": event.phase}
    return None


async def run_turn(
    *,
    agent: Any,
    message: str,
    session_id: str,
    turn_id: str,
    store: ThreadStore,
    bus: SessionBus,
    recursion_limit: int,
) -> TraceRecord:
    """Run one agent turn: stream live frames, append the answer, then mark done."""
    record = TraceRecord(session_id=session_id, turn_id=turn_id)
    inputs = {"messages": [{"role": "user", "content": message}]}
    config = {"recursion_limit": recursion_limit}
    raw_events = agent.astream_events(inputs, config=config, version="v2")
    try:
        async for event in capture(raw_events, record):
            frame = _trace_event_to_frame(event)
            if frame is not None:
                bus.publish(session_id, frame)
        await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="agent_answer",
                content=record.answer,
                turn_id=turn_id,
                trace_id=record.trace_id,
                actor_id="agent",
            ),
        )
    except Exception:
        logger.exception("agent turn failed for session %s", session_id)
        await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="agent_answer",
                content="Sorry, I could not complete that request. Please try again.",
                turn_id=turn_id,
                trace_id=record.trace_id,
                actor_id="agent",
                status="failed",
            ),
        )
        bus.publish(session_id, {"event": "error", "message": "Unable to complete the turn."})
    finally:
        if record.ended_at is None:
            record.finish()
        bus.publish(session_id, {"event": "done", "turn_id": turn_id})
    return record
