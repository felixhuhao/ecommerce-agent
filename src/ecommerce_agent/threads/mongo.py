from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


class MongoThreadStore:
    """Source-of-truth ThreadStore backed by MongoDB via motor."""

    def __init__(self, *, messages: Any, counters: Any, client: Any | None = None) -> None:
        self._messages = messages
        self._counters = counters
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoThreadStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(messages=db["thread_messages"], counters=db["thread_counters"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def append(self, message: ThreadMessage) -> ThreadMessage:
        counter = await self._counters.find_one_and_update(
            {"_id": message.session_id},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        stored = message.model_copy(update={"seq": counter["seq"]})
        await self._messages.insert_one(stored.model_dump())
        return stored

    async def list_messages(self, session_id: str) -> list[ThreadMessage]:
        cursor = self._messages.find({"session_id": session_id}).sort("seq", 1)
        return [
            ThreadMessage(**{key: value for key, value in doc.items() if key != "_id"})
            async for doc in cursor
        ]
