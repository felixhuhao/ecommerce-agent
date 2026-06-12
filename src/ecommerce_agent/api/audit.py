from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ecommerce_agent.audit.query import AuditQuery
from ecommerce_agent.auth.dependencies import require
from ecommerce_agent.auth.models import Action, Actor

router = APIRouter(prefix="/api/audit", tags=["audit"])
AuditActorDep = Annotated[Actor, Depends(require(Action.AUDIT_SEARCH))]


@router.get("/messages")
async def search_messages(
    request: Request,
    _actor: AuditActorDep,
    actor_id: str | None = None,
    approval_id: str | None = None,
    session: str | None = None,
    type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
) -> dict:
    query = AuditQuery(
        actor_id=actor_id,
        approval_id=approval_id,
        session_id=session,
        type=type,
        since=since,
        until=until,
        limit=max(1, min(limit, 500)),
    )
    messages = await request.app.state.audit_store.search(query)
    return {"messages": [message.model_dump() for message in messages]}
