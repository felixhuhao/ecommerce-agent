from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from ecommerce_agent.auth.dependencies import require
from ecommerce_agent.auth.models import Action, Actor
from ecommerce_agent.monitoring.models import AlertStatus
from ecommerce_agent.monitoring.runner import run_monitor_cycle
from ecommerce_agent.monitoring.system import build_monitor_runtime

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
monitor_router = APIRouter(prefix="/api/monitor", tags=["monitor"])
MonitorActorDep = Annotated[Actor, Depends(require(Action.MANAGE_ALERTS))]


def _data(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@router.get("")
async def list_alerts(
    request: Request,
    _actor: MonitorActorDep,
    status_filter: Annotated[AlertStatus | None, Query(alias="status")] = None,
    limit: int = 100,
) -> dict[str, Any]:
    alerts = await request.app.state.alert_store.list(
        status=status_filter,
        limit=max(1, min(limit, 500)),
    )
    return {"alerts": [alert.model_dump(mode="json") for alert in alerts]}


@router.post("/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: str,
    request: Request,
    actor: MonitorActorDep,
) -> dict[str, Any]:
    alert = await request.app.state.alert_store.acknowledge(alert_id, actor_id=actor.user_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    request.app.state.alert_bus.publish(
        {"event": "alert.updated", "alert": alert.model_dump(mode="json")}
    )
    return {"alert": alert.model_dump(mode="json")}


@monitor_router.post("/run")
async def run_monitor(request: Request, _actor: MonitorActorDep) -> dict[str, Any]:
    result = await run_monitor_from_app(request.app)
    if result.get("status") == "already_running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=result)
    return result


@router.get("/stream")
async def stream_alerts(request: Request, _actor: MonitorActorDep) -> EventSourceResponse:
    return EventSourceResponse(_alert_events(request))


async def _alert_events(request: Request) -> AsyncIterator[dict[str, str]]:
    bus = request.app.state.alert_bus
    async with bus.subscription() as sub:
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            yield {"event": event.get("event", ""), "data": _data(event)}


async def run_monitor_from_app(app: Any) -> dict[str, Any]:
    lock = app.state.monitor_run_lock
    # Fast non-blocking guard: manual and scheduled monitor runs share this
    # lock, and a second trigger should report "already running" instead of
    # queueing another cycle.
    if lock.locked():
        return {"status": "already_running"}
    async with lock:
        runtime = await _monitor_runtime(app)
        return await run_monitor_cycle(
            reader=runtime.reader,
            checks=runtime.checks,
            alert_store=app.state.alert_store,
            settings=app.state.settings,
            bus=app.state.alert_bus,
            cause_agent=runtime.cause_agent,
        )


async def _monitor_runtime(app: Any) -> Any:
    factory = getattr(app.state, "monitor_runtime_factory", None)
    if callable(factory):
        result = factory()
        return await result if inspect.isawaitable(result) else result
    runtime = getattr(app.state, "monitor_runtime", None)
    if runtime is None:
        runtime = await build_monitor_runtime(app.state.settings)
        app.state.monitor_runtime = runtime
    return runtime
