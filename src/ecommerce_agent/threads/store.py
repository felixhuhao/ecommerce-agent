from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from ecommerce_agent.threads.messages import ThreadMessage

if TYPE_CHECKING:
    from ecommerce_agent.sessions.bus import SessionBus

logger = logging.getLogger(__name__)


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class ThreadStore(Protocol):
    async def append(self, message: ThreadMessage) -> ThreadMessage:
        """Persist `message`, assigning the next per-session seq. Returns the stored copy."""
        ...

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        """Return all messages for `session_id`, ordered by seq."""
        ...

    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        """Return the newest message for `session_id`, or None."""
        ...

    async def count_messages(self, session_id: str) -> int:
        """Return how many messages `session_id` has."""
        ...

    async def ping(self) -> bool:
        """Return whether the backing store is reachable."""
        ...


class InMemoryThreadStore:
    """Async, test-only ThreadStore. Mongo is the prod source of truth."""

    def __init__(
        self,
        *,
        retention_days: int = 90,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._messages: dict[str, list[ThreadMessage]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._retention_days = retention_days
        self._now = now

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        async with self._lock:
            bucket = self._messages[message.session_id]
            stored = message.model_copy(update={"seq": len(bucket) + 1})
            bucket.append(stored)
            return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        async with self._lock:
            return list(self._messages.get(session_id, ()))

    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        async with self._lock:
            bucket = self._messages.get(session_id, ())
            return bucket[-1] if bucket else None

    async def count_messages(self, session_id: str) -> int:
        async with self._lock:
            return len(self._messages.get(session_id, ()))

    async def ping(self) -> bool:
        return True

    async def sweep_expired(self) -> int:
        cutoff = self._now() - timedelta(days=self._retention_days)
        removed = 0
        async with self._lock:
            for session_id, messages in list(self._messages.items()):
                kept = [
                    message for message in messages if _parse_iso(message.created_at) >= cutoff
                ]
                removed += len(messages) - len(kept)
                if kept:
                    self._messages[session_id] = kept
                else:
                    self._messages.pop(session_id, None)
        return removed


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
        logger.warning(
            "thread.append publish failed for session %s",
            stored.session_id,
            exc_info=True,
        )
    return stored
