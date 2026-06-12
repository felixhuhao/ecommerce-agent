from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore(Protocol):
    async def create(self, session_id: str, *, owner_id: str) -> None:
        """Create a durable session record if it does not already exist."""
        ...

    async def exists(self, session_id: str) -> bool:
        """Return whether `session_id` is known."""
        ...

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """Return a session record, or None."""
        ...

    async def set_title_if_absent(self, session_id: str, title: str) -> None:
        """Set the title once from the first user message."""
        ...

    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        """Return session records newest-first."""
        ...

    async def backfill_ownerless(self, *, owner_id: str) -> int:
        """Assign `owner_id` to records that predate session ownership."""
        ...


class InMemorySessionStore:
    """Async, test-only SessionStore."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._order = itertools.count()
        self._seq: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, *, owner_id: str) -> None:
        async with self._lock:
            if session_id in self._records:
                return
            self._records[session_id] = {
                "session_id": session_id,
                "owner_id": owner_id,
                "title": None,
                "created_at": _now_iso(),
            }
            self._seq[session_id] = next(self._order)

    async def exists(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._records

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(session_id)
            return dict(record) if record else None

    async def set_title_if_absent(self, session_id: str, title: str) -> None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is not None and record["title"] is None:
                record["title"] = title

    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            records = [
                dict(record)
                for _, record in sorted(
                    self._records.items(),
                    key=lambda item: self._seq[item[0]],
                    reverse=True,
                )
            ]
        if owner_id is not None:
            records = [record for record in records if record.get("owner_id") == owner_id]
        return records

    async def backfill_ownerless(self, *, owner_id: str) -> int:
        count = 0
        async with self._lock:
            for record in self._records.values():
                if record.get("owner_id") is None:
                    record["owner_id"] = owner_id
                    count += 1
        return count


class MongoSessionStore:
    """Source-of-truth SessionStore backed by MongoDB via motor."""

    def __init__(self, *, sessions: Any, client: Any | None = None) -> None:
        self._sessions = sessions
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoSessionStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(sessions=db["sessions"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def create(self, session_id: str, *, owner_id: str) -> None:
        await self._sessions.update_one(
            {"_id": session_id},
            {"$setOnInsert": {"owner_id": owner_id, "title": None, "created_at": _now_iso()}},
            upsert=True,
        )

    async def exists(self, session_id: str) -> bool:
        return await self._sessions.count_documents({"_id": session_id}, limit=1) > 0

    async def get(self, session_id: str) -> dict[str, Any] | None:
        doc = await self._sessions.find_one({"_id": session_id})
        return self._to_record(doc) if doc else None

    async def set_title_if_absent(self, session_id: str, title: str) -> None:
        await self._sessions.update_one(
            {"_id": session_id, "title": None},
            {"$set": {"title": title}},
        )

    async def list_records(self, *, owner_id: str | None = None) -> list[dict[str, Any]]:
        query = {"owner_id": owner_id} if owner_id is not None else {}
        cursor = self._sessions.find(query).sort("created_at", -1)
        return [self._to_record(doc) async for doc in cursor]

    async def backfill_ownerless(self, *, owner_id: str) -> int:
        result = await self._sessions.update_many(
            {"owner_id": {"$exists": False}},
            {"$set": {"owner_id": owner_id}},
        )
        return int(result.modified_count)

    @staticmethod
    def _to_record(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "session_id": doc["_id"],
            "owner_id": doc.get("owner_id"),
            "title": doc.get("title"),
            "created_at": doc.get("created_at"),
        }
