from __future__ import annotations

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

    def __init__(self) -> None:
        self._records: dict[tuple[str | None, str | None], TraceRecord] = {}

    async def save(self, record: TraceRecord) -> None:
        self._records[(record.session_id, record.turn_id)] = record

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        return self._records.get((session_id, turn_id))

    async def ping(self) -> bool:
        return True
