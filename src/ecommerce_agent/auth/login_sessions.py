from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

from ecommerce_agent.config import Settings


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LoginSessionStore(Protocol):
    async def create(self, user_id: str, *, ttl_seconds: int) -> str: ...
    async def get(self, session_id: str) -> dict[str, Any] | None: ...
    async def delete(self, session_id: str) -> None: ...


class InMemoryLoginSessionStore:
    def __init__(self, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.now = now
        self._records: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, user_id: str, *, ttl_seconds: int) -> str:
        session_id = secrets.token_urlsafe(32)
        now = self.now()
        async with self._lock:
            self._records[session_id] = {
                "user_id": user_id,
                "created_at": now,
                "expire_at": now + timedelta(seconds=ttl_seconds),
            }
        return session_id

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            record = self._records.get(session_id)
            if record is None:
                return None
            if record["expire_at"] <= self.now():
                del self._records[session_id]
                return None
            return dict(record)

    async def delete(self, session_id: str) -> None:
        async with self._lock:
            self._records.pop(session_id, None)


class MongoLoginSessionStore:
    def __init__(self, *, sessions: Any, client: Any | None = None) -> None:
        self._sessions = sessions
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoLoginSessionStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(sessions=db["auth_sessions"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._sessions.create_index("expire_at", expireAfterSeconds=0)

    async def create(self, user_id: str, *, ttl_seconds: int) -> str:
        session_id = secrets.token_urlsafe(32)
        now = _utcnow()
        await self._sessions.insert_one(
            {
                "_id": session_id,
                "user_id": user_id,
                "created_at": now,
                "expire_at": now + timedelta(seconds=ttl_seconds),
            }
        )
        return session_id

    async def get(self, session_id: str) -> dict[str, Any] | None:
        doc = await self._sessions.find_one({"_id": session_id})
        if doc is None:
            return None
        expire_at = doc["expire_at"]
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        if expire_at <= _utcnow():
            await self.delete(session_id)
            return None
        return {"user_id": doc["user_id"], "created_at": doc["created_at"], "expire_at": expire_at}

    async def delete(self, session_id: str) -> None:
        await self._sessions.delete_one({"_id": session_id})
