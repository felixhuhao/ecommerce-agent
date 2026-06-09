from __future__ import annotations

import logging
from typing import Any

from ecommerce_agent.approvals import approval_card
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


def _request_approval_events(record: TraceRecord) -> list[TraceEvent]:
    return [
        event
        for event in record.events
        if event.event_type == "tool_call"
        and event.name == "request_approval"
        and event.phase == "end"
    ]


def _proposal_failure_message(
    *,
    session_id: str,
    turn_id: str,
    trace_id: str,
) -> ThreadMessage:
    return ThreadMessage(
        session_id=session_id,
        type="agent_answer",
        content=(
            "The action proposal was requested, but I could not read the approval id. "
            "No proposal was added to the thread."
        ),
        turn_id=turn_id,
        trace_id=trace_id,
        actor_id="agent",
        status="failed",
    )


async def _append_turn_result(
    *,
    record: TraceRecord,
    session_id: str,
    turn_id: str,
    store: ThreadStore,
    bus: SessionBus,
    approval_client: Any | None,
) -> None:
    approval_events = _request_approval_events(record)
    if not approval_events:
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
        return

    approval_id = next((event.approval_id for event in reversed(approval_events)), None)
    if not approval_id or approval_client is None:
        await append_and_publish(
            store,
            bus,
            _proposal_failure_message(
                session_id=session_id,
                turn_id=turn_id,
                trace_id=record.trace_id,
            ),
        )
        return

    approval = await approval_client.get_approval(approval_id)
    await append_and_publish(
        store,
        bus,
        ThreadMessage(
            session_id=session_id,
            type="agent_proposal",
            content=record.answer or f"Approval {approval_id} is pending.",
            turn_id=turn_id,
            trace_id=record.trace_id,
            actor_id="agent",
            approval_id=approval_id,
            card=approval_card(approval),
            tool_name=approval.get("toolName"),
            status=approval.get("status") or "pending",
        ),
    )


async def run_turn(
    *,
    agent: Any,
    message: str,
    session_id: str,
    turn_id: str,
    store: ThreadStore,
    bus: SessionBus,
    recursion_limit: int,
    approval_client: Any | None = None,
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
        await _append_turn_result(
            record=record,
            session_id=session_id,
            turn_id=turn_id,
            store=store,
            bus=bus,
            approval_client=approval_client,
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
