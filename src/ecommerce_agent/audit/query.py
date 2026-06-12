from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ecommerce_agent.threads.messages import ThreadMessage


@dataclass
class AuditQuery:
    actor_id: str | None = None
    approval_id: str | None = None
    session_id: str | None = None
    type: str | None = None
    since: str | None = None
    until: str | None = None
    limit: int = 100


class AuditStore(Protocol):
    async def search(self, query: AuditQuery) -> list[ThreadMessage]: ...


class InMemoryAuditStore:
    def __init__(self, messages: list[ThreadMessage] | None = None) -> None:
        self._messages = list(messages or [])

    async def search(self, query: AuditQuery) -> list[ThreadMessage]:
        results = [message for message in self._messages if _matches(message, query)]
        results.sort(key=lambda message: message.created_at, reverse=True)
        return results[: query.limit]


def _matches(message: ThreadMessage, query: AuditQuery) -> bool:
    if query.actor_id is not None and message.actor_id != query.actor_id:
        return False
    if query.approval_id is not None and message.approval_id != query.approval_id:
        return False
    if query.session_id is not None and message.session_id != query.session_id:
        return False
    if query.type is not None and message.type != query.type:
        return False
    if query.since is not None and message.created_at < query.since:
        return False
    if query.until is not None and message.created_at >= query.until:
        return False
    return True
