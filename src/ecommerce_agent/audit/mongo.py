from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.audit.query import AuditQuery
from ecommerce_agent.config import Settings
from ecommerce_agent.threads.messages import ThreadMessage


class MongoAuditStore:
    """Read-only cross-session view over the thread messages collection."""

    def __init__(self, *, messages: Any, client: Any | None = None) -> None:
        self._messages = messages
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoAuditStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(messages=db["thread_messages"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._messages.create_index("actor_id")
        await self._messages.create_index("approval_id")
        await self._messages.create_index("created_at")

    async def search(self, query: AuditQuery) -> list[ThreadMessage]:
        mongo_query: dict[str, Any] = {}
        if query.actor_id is not None:
            mongo_query["actor_id"] = query.actor_id
        if query.approval_id is not None:
            mongo_query["approval_id"] = query.approval_id
        if query.session_id is not None:
            mongo_query["session_id"] = query.session_id
        if query.type is not None:
            mongo_query["type"] = query.type
        created: dict[str, Any] = {}
        if query.since is not None:
            created["$gte"] = query.since
        if query.until is not None:
            created["$lt"] = query.until
        if created:
            mongo_query["created_at"] = created

        cursor = self._messages.find(mongo_query).sort("created_at", -1).limit(query.limit)
        return [
            ThreadMessage(
                **{key: value for key, value in doc.items() if key not in ("_id", "expire_at")}
            )
            async for doc in cursor
        ]
