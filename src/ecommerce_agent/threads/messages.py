from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

MessageType = Literal[
    "user",
    "agent_answer",
    "agent_proposal",
    "approval_status",
    "execution_result",
]


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class ThreadMessage(BaseModel):
    """One appended message in a session's conversation thread.

    `seq` is the per-session monotonic ordering key, assigned by the ThreadStore
    on append (0 means unassigned). Ordering and dedupe use `seq`, never `created_at`.
    """

    message_id: str = Field(default_factory=_new_id)
    session_id: str
    seq: int = 0
    type: MessageType
    content: str = ""
    created_at: str = Field(default_factory=_now_iso)

    # Audit / correlation spine.
    turn_id: str | None = None
    trace_id: str | None = None
    actor_id: str | None = None
    execution_id: str | None = None

    # Type-specific fields.
    approval_id: str | None = None
    card: dict[str, Any] | None = None
    tool_name: str | None = None
    status: str | None = None
    result: dict[str, Any] | None = None
    grounding: dict[str, Any] | None = None
    reason: str | None = None
