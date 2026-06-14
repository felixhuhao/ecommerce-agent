from __future__ import annotations

import asyncio
from types import TracebackType


class AlertSubscription:
    def __init__(self, bus: AlertBus) -> None:
        self._bus = bus
        self.queue: asyncio.Queue[dict] = asyncio.Queue()

    async def __aenter__(self) -> AlertSubscription:
        self._bus._add(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._bus._remove(self)


class AlertBus:
    """In-process alert pub/sub. Single-instance only."""

    def __init__(self) -> None:
        self._subscribers: set[AlertSubscription] = set()

    def subscription(self) -> AlertSubscription:
        return AlertSubscription(self)

    def publish(self, event: dict) -> None:
        for sub in list(self._subscribers):
            sub.queue.put_nowait(event)

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _add(self, sub: AlertSubscription) -> None:
        self._subscribers.add(sub)

    def _remove(self, sub: AlertSubscription) -> None:
        self._subscribers.discard(sub)

