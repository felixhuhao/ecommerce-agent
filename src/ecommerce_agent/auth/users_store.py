from __future__ import annotations

import asyncio
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from ecommerce_agent.auth.models import User
from ecommerce_agent.config import Settings


class UserStore(Protocol):
    async def create(self, user: User) -> None: ...
    async def get_by_username(self, username: str) -> User | None: ...
    async def get_by_id(self, user_id: str) -> User | None: ...


class InMemoryUserStore:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}
        self._by_username: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, user: User) -> None:
        async with self._lock:
            if user.username in self._by_username:
                raise ValueError(f"username already exists: {user.username}")
            self._by_id[user.user_id] = user
            self._by_username[user.username] = user.user_id

    async def get_by_username(self, username: str) -> User | None:
        user_id = self._by_username.get(username)
        return self._by_id.get(user_id) if user_id else None

    async def get_by_id(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)


class MongoUserStore:
    def __init__(self, *, users: Any, client: Any | None = None) -> None:
        self._users = users
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> MongoUserStore:
        client = AsyncIOMotorClient(settings.mongo_url)
        db = client[settings.mongo_db]
        return cls(users=db["users"], client=client)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    async def ensure_indexes(self) -> None:
        await self._users.create_index("username", unique=True)

    async def create(self, user: User) -> None:
        try:
            await self._users.insert_one({"_id": user.user_id, **user.model_dump()})
        except DuplicateKeyError as exc:
            raise ValueError(f"username already exists: {user.username}") from exc

    async def get_by_username(self, username: str) -> User | None:
        doc = await self._users.find_one({"username": username})
        return self._to_user(doc) if doc else None

    async def get_by_id(self, user_id: str) -> User | None:
        doc = await self._users.find_one({"_id": user_id})
        return self._to_user(doc) if doc else None

    @staticmethod
    def _to_user(doc: dict[str, Any]) -> User:
        return User(**{key: value for key, value in doc.items() if key != "_id"})
