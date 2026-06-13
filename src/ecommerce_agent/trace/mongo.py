from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings
from ecommerce_agent.trace.schema import TraceRecord


class MongoTraceStore:
    """Source-of-truth TraceStore backed by MongoDB via motor."""

    def __init__(
        self,
        *,
        traces: Any,
        client: Any | None = None,
        retention_days: int = 90,
    ) -> None:
        self._traces = traces
        self._client = client
        self._retention_days = retention_days
        self._indexed = False
        self._index_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoTraceStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(
            traces=db["traces"],
            client=client,
            retention_days=settings.audit_retention_days,
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        if self._indexed:
            return
        async with self._index_lock:
            if self._indexed:
                return
            await self._traces.create_index([("session_id", 1), ("turn_id", 1)], unique=True)
            await self._traces.create_index("expire_at", expireAfterSeconds=0)
            self._indexed = True

    async def save(self, record: TraceRecord) -> None:
        await self.ensure_indexes()
        doc = record.to_dict()
        doc["expire_at"] = datetime.now(UTC) + timedelta(days=self._retention_days)
        await self._traces.update_one(
            {"session_id": record.session_id, "turn_id": record.turn_id},
            {"$set": doc},
            upsert=True,
        )

    async def get(self, session_id: str, turn_id: str) -> TraceRecord | None:
        doc = await self._traces.find_one({"session_id": session_id, "turn_id": turn_id})
        if doc is None:
            return None
        return TraceRecord.from_dict(
            {key: value for key, value in doc.items() if key not in ("_id", "expire_at")}
        )

    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True
