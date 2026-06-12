from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from ecommerce_agent.auth.models import Action, Actor
from ecommerce_agent.auth.permissions import can


async def current_actor(request: Request) -> Actor:
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.auth_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    record = await request.app.state.login_session_store.get(cookie)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="session expired")
    user = await request.app.state.user_store.get_by_id(record["user_id"])
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown user")
    return Actor.from_user(user)


def require(action: Action) -> Callable[..., Awaitable[Actor]]:
    async def dependency(actor: Annotated[Actor, Depends(current_actor)]) -> Actor:
        if not can(actor.role, action):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return actor

    return dependency
