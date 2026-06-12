from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


class MongoThreadStore:
    """Source-of-truth ThreadStore backed by MongoDB via motor."""

    def __init__(
        self,
        *,
        messages: Any,
        counters: Any,
        client: Any | None = None,
        retention_days: int = 90,
    ) -> None:
        self._messages = messages
        self._counters = counters
        self._client = client
        self._retention_days = retention_days

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoThreadStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(
            messages=db["thread_messages"],
            counters=db["thread_counters"],
            client=client,
            retention_days=settings.audit_retention_days,
        )

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._messages.create_index("expire_at", expireAfterSeconds=0)
        await self._messages.create_index("actor_id")
        await self._messages.create_index("approval_id")
        await self._messages.create_index("created_at")

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        counter = await self._counters.find_one_and_update(
            {"_id": message.session_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        stored = message.model_copy(update={"seq": counter["seq"]})
        doc = stored.model_dump()
        doc["expire_at"] = datetime.now(UTC) + timedelta(days=self._retention_days)
        await self._messages.insert_one(doc)
        return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        cursor = self._messages.find({"session_id": session_id}).sort("seq", 1)
        return [
            ThreadMessage(
                **{key: value for key, value in doc.items() if key not in ("_id", "expire_at")}
            )
            async for doc in cursor
        ]

    async def latest_message(self, session_id: str) -> ThreadMessage | None:
        doc = await self._messages.find_one({"session_id": session_id}, sort=[("seq", -1)])
        if doc is None:
            return None
        return ThreadMessage(
            **{key: value for key, value in doc.items() if key not in ("_id", "expire_at")}
        )

    async def count_messages(self, session_id: str) -> int:
        return await self._messages.count_documents({"session_id": session_id})

    async def ping(self) -> bool:
        if self._client is None:
            return False
        await self._client.admin.command("ping")
        return True
