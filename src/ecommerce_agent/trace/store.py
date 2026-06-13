from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ecommerce_agent.trace.schema import TraceRecord


class TraceStore(Protocol):
    async def save(self, record: TraceRecord) -> None:
        """Persist one turn's trace, upserting by (session_id, turn_id)."""
        ...

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        """Return the trace for one turn, or None."""
        ...

    async def ping(self) -> bool:
        """Return whether the backing store is reachable."""
        ...


class InMemoryTraceStore:
    """Async, test-only TraceStore. Mongo is the prod source of truth."""

    def __init__(
        self,
        *,
        retention_days: int = 90,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._records: dict[tuple[str | None, str | None], TraceRecord] = {}
        self._lock = asyncio.Lock()
        self._retention_days = retention_days
        self._now = now

    async def save(self, record: TraceRecord) -> None:
        async with self._lock:
            self._records[(record.session_id, record.turn_id)] = record

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        async with self._lock:
            return self._records.get((session_id, turn_id))

    async def ping(self) -> bool:
        return True

    async def sweep_expired(self) -> int:
        cutoff = (self._now() - timedelta(days=self._retention_days)).timestamp()
        async with self._lock:
            expired = [
                key
                for key, record in self._records.items()
                if (record.ended_at or record.started_at) < cutoff
            ]
            for key in expired:
                self._records.pop(key, None)
        return len(expired)
