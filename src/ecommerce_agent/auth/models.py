from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"


class Action(StrEnum):
    PROPOSE = "propose"
    APPROVE = "approve"
    AUDIT_SEARCH = "audit_search"


class User(BaseModel):
    user_id: str
    username: str
    password_hash: str
    role: Role
    spring_user_id: int
    created_at: str


class Actor(BaseModel):
    """Resolved request principal. Carries no secret."""

    user_id: str
    username: str
    role: Role
    spring_user_id: int

    @classmethod
    def from_user(cls, user: User) -> Actor:
        return cls(
            user_id=user.user_id,
            username=user.username,
            role=user.role,
            spring_user_id=user.spring_user_id,
        )
