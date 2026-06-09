from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Protocol

from ecommerce_agent.threads.messages import ThreadMessage

if TYPE_CHECKING:
    from ecommerce_agent.sessions.bus import SessionBus

logger = logging.getLogger(__name__)


class ThreadStore(Protocol):
    async def append(self, message: ThreadMessage) -> ThreadMessage:
        """Persist `message`, assigning the next per-session seq. Returns the stored copy."""
        ...

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        """Return all messages for `session_id`, ordered by seq."""
        ...


class InMemoryThreadStore:
    """Async, test-only ThreadStore. Mongo is the prod source of truth."""

    def __init__(self) -> None:
        self._messages: dict[str, list[ThreadMessage]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        async with self._lock:
            bucket = self._messages[message.session_id]
            stored = message.model_copy(update={"seq": len(bucket) + 1})
            bucket.append(stored)
            return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        async with self._lock:
            return list(self._messages.get(session_id, ()))


async def append_and_publish(
    store: ThreadStore,
    bus: SessionBus,
    message: ThreadMessage,
) -> ThreadMessage:
    """Persist first, then best-effort publish a thread.append event."""
    stored = await store.append(message)
    try:
        bus.publish(stored.session_id, {"event": "thread.append", "message": stored.model_dump()})
    except Exception:
        logger.warning("thread.append publish failed for session %s", stored.session_id, exc_info=True)
    return stored
