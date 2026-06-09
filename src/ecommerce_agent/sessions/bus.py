from __future__ import annotations

import asyncio
from types import TracebackType


class Subscription:
    """A single live subscriber. The SSE endpoint drains `queue`."""

    def __init__(self, bus: SessionBus, session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self.queue: asyncio.Queue[dict] = asyncio.Queue()

    async def __aenter__(self) -> Subscription:
        self._bus._add(self._session_id, self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bus._remove(self._session_id, self)


class SessionBus:
    """In-process per-session pub/sub. Single-instance only."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = {}

    def subscription(self, session_id: str) -> Subscription:
        return Subscription(self, session_id)

    def publish(self, session_id: str, event: dict) -> None:
        for sub in list(self._subscribers.get(session_id, ())):
            sub.queue.put_nowait(event)

    def subscriber_count(self, session_id: str) -> int:
        return len(self._subscribers.get(session_id, ()))

    def _add(self, session_id: str, sub: Subscription) -> None:
        self._subscribers.setdefault(session_id, set()).add(sub)

    def _remove(self, session_id: str, sub: Subscription) -> None:
        subs = self._subscribers.get(session_id)
        if not subs:
            return
        subs.discard(sub)
        if not subs:
            self._subscribers.pop(session_id, None)
