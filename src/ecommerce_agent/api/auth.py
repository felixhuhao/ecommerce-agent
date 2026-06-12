from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, StringConstraints

from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.models import Actor
from ecommerce_agent.auth.passwords import verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    password: Annotated[str, StringConstraints(min_length=1)]


def _actor_public(actor: Actor) -> dict:
    return {
        "user_id": actor.user_id,
        "username": actor.username,
        "role": actor.role,
        "spring_user_id": actor.spring_user_id,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    user = await request.app.state.user_store.get_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    session_id = await request.app.state.login_session_store.create(
        user.user_id,
        ttl_seconds=settings.auth_session_ttl_seconds,
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=session_id,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return _actor_public(Actor.from_user(user))


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    settings = request.app.state.settings
    cookie = request.cookies.get(settings.auth_cookie_name)
    if cookie:
        await request.app.state.login_session_store.delete(cookie)
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
async def me(actor: Annotated[Actor, Depends(current_actor)]) -> dict:
    return _actor_public(actor)
