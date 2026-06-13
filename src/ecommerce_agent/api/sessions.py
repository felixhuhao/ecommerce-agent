from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.approvals import ApprovalApiError, execute_with_retry, make_approval_client
from ecommerce_agent.auth.dependencies import current_actor, require
from ecommerce_agent.auth.models import Action, Actor
from ecommerce_agent.auth.permissions import can
from ecommerce_agent.sessions.registry import RuntimeActor
from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import append_and_publish
from ecommerce_agent.trace.projection import project_timeline
from ecommerce_agent.trace.schema import TraceRecord

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)
ActorDep = Annotated[Actor, Depends(current_actor)]
ApproveActorDep = Annotated[Actor, Depends(require(Action.APPROVE))]


class MessageRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RejectApprovalRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


def _data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _approval_client(request: Request, session_id: str, actor: Actor) -> Any:
    factory = getattr(request.app.state, "approval_client_factory", None)
    if callable(factory):
        return factory(session_id)
    user_id = str(actor.spring_user_id)
    clients = getattr(request.app.state, "approval_clients", None)
    if isinstance(clients, dict):
        cache_key = (session_id, actor.user_id)
        client = clients.get(cache_key)
        if client is None:
            client = make_approval_client(
                request.app.state.settings,
                session_id=session_id,
                user_id=user_id,
            )
            clients[cache_key] = client
        return client
    return make_approval_client(
        request.app.state.settings,
        session_id=session_id,
        user_id=user_id,
    )


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if not key.startswith("_")}


def _result_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return value if isinstance(value, dict) else {"value": value}


async def _ensure_approval_visible(
    *,
    client: Any,
    approval_id: str,
) -> None:
    await client.get_approval(approval_id)


def _decision_changed(decision: dict[str, Any]) -> bool:
    return decision.get("changed") is not False and decision.get("_http_status_code") != 409


def _approval_status_content(approval_id: str, status_value: str, reason: str | None) -> str:
    suffix = f": {reason}" if reason else "."
    return f"Approval {approval_id} {status_value}{suffix}"


def _execution_status_reason(execution: dict[str, Any]) -> str | None:
    message = execution.get("message")
    if execution.get("status") == "invalidated":
        suffix = "Request a fresh approval."
        if not message:
            return suffix
        if "fresh approval" not in message.lower():
            return f"{message}. {suffix}"
    return message


async def _append_approval_status(
    *,
    request: Request,
    session_id: str,
    approval_id: str,
    status_value: str,
    actor_id: str,
    reason: str | None = None,
    result: dict[str, Any] | None = None,
) -> ThreadMessage:
    return await append_and_publish(
        request.app.state.thread_store,
        request.app.state.session_bus,
        ThreadMessage(
            session_id=session_id,
            type="approval_status",
            content=_approval_status_content(approval_id, status_value, reason),
            actor_id=actor_id,
            approval_id=approval_id,
            status=status_value,
            reason=reason,
            result=result,
        ),
    )


async def _existing_execution_result(
    *,
    request: Request,
    session_id: str,
    approval_id: str,
) -> ThreadMessage | None:
    messages = await request.app.state.thread_store.list_messages(session_id)
    for message in reversed(messages):
        if message.type == "execution_result" and message.approval_id == approval_id:
            return message
    return None


async def _append_execution_result(
    *,
    request: Request,
    session_id: str,
    approval_id: str,
    execution: dict[str, Any],
    actor_id: str,
) -> ThreadMessage:
    existing = await _existing_execution_result(
        request=request,
        session_id=session_id,
        approval_id=approval_id,
    )
    if existing is not None:
        return existing

    result = _result_dict(execution.get("executionResult"))
    return await append_and_publish(
        request.app.state.thread_store,
        request.app.state.session_bus,
        ThreadMessage(
            session_id=session_id,
            type="execution_result",
            content=execution.get("message") or f"Approval {approval_id} executed.",
            actor_id=actor_id,
            approval_id=approval_id,
            status=execution.get("status") or "consumed",
            result=result,
        ),
    )


def _raise_approval_error(exc: ApprovalApiError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.payload) from exc


def _title_from_message(message: str) -> str:
    return message.strip()[:80]


async def _require_owned_session(request: Request, session_id: str, actor: Actor) -> dict[str, Any]:
    record = await request.app.state.session_store.get(session_id)
    if record is None or record.get("owner_id") != actor.user_id:
        raise HTTPException(status_code=404, detail="session not found")
    return record


async def _load_trace_record(request: Request, session_id: str, turn_id: str) -> TraceRecord | None:
    try:
        record = await request.app.state.trace_store.get(session_id, turn_id)
    except Exception:
        logger.exception("trace_store.get failed for %s/%s", session_id, turn_id)
        record = None
    if record is not None:
        return record
    return request.app.state.trace_records.get(session_id, {}).get(turn_id)


def _session_artifacts(messages: list[ThreadMessage]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for message in reversed(messages):
        items = (message.result or {}).get("artifacts")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            artifacts.append(
                {
                    "id": item.get("id"),
                    "kind": item.get("kind"),
                    "mime_type": item.get("mime_type"),
                    "src": item.get("src"),
                    "tool_name": item.get("tool_name"),
                    "turn_id": message.turn_id,
                    "trace_id": message.trace_id,
                    "created_at": message.created_at,
                    "message_id": message.message_id,
                }
            )
    return artifacts


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(request: Request, actor: ActorDep) -> dict[str, str]:
    runtime_actor = RuntimeActor(
        user_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
        can_propose=can(actor.role, Action.PROPOSE),
    )
    session_id = await request.app.state.session_registry.create(runtime_actor)
    await request.app.state.session_store.create(session_id, owner_id=actor.user_id)
    return {"session_id": session_id}


@router.get("")
async def list_sessions(request: Request, actor: ActorDep) -> dict[str, Any]:
    store = request.app.state.session_store
    thread_store = request.app.state.thread_store
    summaries = []
    for record in await store.list_records(owner_id=actor.user_id):
        session_id = record["session_id"]
        latest = await thread_store.latest_message(session_id)
        summaries.append(
            {
                **record,
                "last_message_preview": latest.content[:120] if latest else None,
                "message_count": await thread_store.count_messages(session_id),
            }
        )
    return {"sessions": summaries}


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request, actor: ActorDep) -> dict[str, Any]:
    record = await _require_owned_session(request, session_id, actor)
    return {
        **record,
        "message_count": await request.app.state.thread_store.count_messages(session_id),
    }


@router.get("/{session_id}/thread")
async def get_thread(session_id: str, request: Request, actor: ActorDep) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    messages = await request.app.state.thread_store.list_messages(session_id)
    return {"session_id": session_id, "messages": [message.model_dump() for message in messages]}


@router.get("/{session_id}/artifacts")
async def list_artifacts(session_id: str, request: Request, actor: ActorDep) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    messages = await request.app.state.thread_store.list_messages(session_id)
    return {"session_id": session_id, "artifacts": _session_artifacts(messages)}


@router.get("/{session_id}/turns/{turn_id}/trace")
async def get_trace(
    session_id: str,
    turn_id: str,
    request: Request,
    actor: ActorDep,
) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    record = await _load_trace_record(request, session_id, turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return project_timeline(record)


@router.get("/{session_id}/turns/{turn_id}/trace/export")
async def export_trace(
    session_id: str,
    turn_id: str,
    request: Request,
    actor: ActorDep,
) -> JSONResponse:
    await _require_owned_session(request, session_id, actor)
    record = await _load_trace_record(request, session_id, turn_id)
    if record is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return JSONResponse(
        content=record.to_dict(),
        headers={"Content-Disposition": f'attachment; filename="trace-{turn_id}.json"'},
    )


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def post_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
    actor: ActorDep,
) -> dict[str, Any]:
    registry = request.app.state.session_registry
    session_store = request.app.state.session_store
    await _require_owned_session(request, session_id, actor)

    if not await registry.try_begin_turn(session_id):
        raise HTTPException(status_code=409, detail={"error": "turn_in_progress"})

    runtime_actor = RuntimeActor(
        user_id=actor.user_id,
        spring_user_id=actor.spring_user_id,
        can_propose=can(actor.role, Action.PROPOSE),
    )

    async def _known(sid: str) -> bool:
        record = await session_store.get(sid)
        return record is not None and record.get("owner_id") == actor.user_id

    try:
        runtime = await registry.get_or_create_runtime(session_id, runtime_actor, _known)
    except KeyError as exc:
        await registry.end_turn(session_id)
        raise HTTPException(status_code=404, detail="session not found") from exc
    except PermissionError as exc:
        await registry.end_turn(session_id)
        raise HTTPException(status_code=403, detail="forbidden") from exc
    except Exception:
        await registry.end_turn(session_id)
        raise

    store = request.app.state.thread_store
    bus = request.app.state.session_bus
    settings = request.app.state.settings
    turn_id = uuid.uuid4().hex

    try:
        user_message = await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="user",
                content=payload.message,
                actor_id=actor.user_id,
                turn_id=turn_id,
            ),
        )
        await session_store.set_title_if_absent(session_id, _title_from_message(payload.message))
    except Exception:
        await registry.end_turn(session_id)
        raise

    app_state = request.app.state
    approval_client = _approval_client(request, session_id, actor)

    async def run_and_record_trace() -> None:
        try:
            record = await run_turn(
                agent=runtime.agent,
                message=payload.message,
                session_id=session_id,
                turn_id=turn_id,
                store=store,
                bus=bus,
                recursion_limit=settings.agent_recursion_limit,
                approval_client=approval_client,
            )
            trace_records = app_state.trace_records
            trace_records.setdefault(session_id, {})[turn_id] = record
            # Compatibility shortcut for the sequential live reliability harness.
            app_state.last_trace = record
            trace_store = getattr(app_state, "trace_store", None)
            if trace_store is not None:
                try:
                    await trace_store.save(record)
                except Exception:
                    logger.exception("failed to persist trace for %s/%s", session_id, turn_id)
        finally:
            await registry.end_turn(session_id)

    task = asyncio.create_task(run_and_record_trace())
    background_tasks = request.app.state.background_tasks
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

    return {"turn_id": turn_id, "user_message_id": user_message.message_id}


@router.post("/{session_id}/approvals/{approval_id}/approve")
async def approve_approval(
    session_id: str,
    approval_id: str,
    request: Request,
    actor: ApproveActorDep,
) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    client = _approval_client(request, session_id, actor)
    actor_id = actor.user_id
    try:
        await _ensure_approval_visible(client=client, approval_id=approval_id)
        decision = await client.approve(approval_id)
        if _decision_changed(decision):
            await _append_approval_status(
                request=request,
                session_id=session_id,
                approval_id=approval_id,
                status_value=decision.get("status") or "approved",
                actor_id=actor_id,
                reason=decision.get("rejectionReason"),
            )
        execution = await execute_with_retry(client, approval_id)
    except ApprovalApiError as exc:
        _raise_approval_error(exc)

    execution_status = execution.get("status") or "unknown"
    if execution_status == "consumed":
        message = await _append_execution_result(
            request=request,
            session_id=session_id,
            approval_id=approval_id,
            execution=execution,
            actor_id=actor_id,
        )
    else:
        message = await _append_approval_status(
            request=request,
            session_id=session_id,
            approval_id=approval_id,
            status_value=execution_status,
            actor_id=actor_id,
            reason=_execution_status_reason(execution),
            result=_result_dict(execution.get("executionResult")),
        )

    return {
        "approval": _public_payload(decision),
        "execution": _public_payload(execution),
        "message": message.model_dump(),
    }


@router.post("/{session_id}/approvals/{approval_id}/reject")
async def reject_approval(
    session_id: str,
    approval_id: str,
    request: Request,
    actor: ApproveActorDep,
    payload: RejectApprovalRequest | None = None,
) -> dict[str, Any]:
    await _require_owned_session(request, session_id, actor)
    client = _approval_client(request, session_id, actor)
    actor_id = actor.user_id
    reason = payload.reason if payload else None
    try:
        await _ensure_approval_visible(client=client, approval_id=approval_id)
        decision = await client.reject(approval_id, reason=reason)
    except ApprovalApiError as exc:
        _raise_approval_error(exc)

    if not _decision_changed(decision):
        raise HTTPException(status_code=409, detail=_public_payload(decision))

    message = await _append_approval_status(
        request=request,
        session_id=session_id,
        approval_id=approval_id,
        status_value=decision.get("status") or "rejected",
        actor_id=actor_id,
        reason=decision.get("rejectionReason") or reason,
    )
    return {"approval": _public_payload(decision), "message": message.model_dump()}


@router.get("/{session_id}/stream")
async def stream(session_id: str, request: Request, actor: ActorDep) -> EventSourceResponse:
    await _require_owned_session(request, session_id, actor)
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
            evt_name = event.get("event", "")
            if (
                evt_name == "thread.append"
                and isinstance(event.get("message"), dict)
                and isinstance(event["message"].get("seq"), int)
                and event["message"]["seq"] <= cursor
            ):
                continue
            yield {"event": evt_name, "data": _data(event)}
