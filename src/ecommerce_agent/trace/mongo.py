from __future__ import annotations

import asyncio
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings
from ecommerce_agent.trace.schema import TraceRecord


class MongoTraceStore:
    """Source-of-truth TraceStore backed by MongoDB via motor."""

    def __init__(self, *, traces: Any, client: Any | None = None) -> None:
        self._traces = traces
        self._client = client
        self._indexed = False
        self._index_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoTraceStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(traces=db["traces"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def _ensure_indexes(self) -> None:
        if self._indexed:
            return
        async with self._index_lock:
            if self._indexed:
                return
            await self._traces.create_index([("session_id", 1), ("turn_id", 1)], unique=True)
            self._indexed = True

    async def save(self, record: TraceRecord) -> None:
        await self._ensure_indexes()
        await self._traces.update_one(
            {"session_id": record.session_id, "turn_id": record.turn_id},
            {"$set": record.to_dict()},
            upsert=True,
        )

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        doc = await self._traces.find_one({"session_id": session_id, "turn_id": turn_id})
        if doc is None:
            return None
        return TraceRecord.from_dict({key: value for key, value in doc.items() if key != "_id"})

    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True
