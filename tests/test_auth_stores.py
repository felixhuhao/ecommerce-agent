from datetime import UTC, datetime, timedelta

import pytest

from ecommerce_agent.auth.login_sessions import InMemoryLoginSessionStore
from ecommerce_agent.auth.models import Role, User
from ecommerce_agent.auth.users_store import InMemoryUserStore


def _user(username: str = "alice") -> User:
    return User(
        user_id=f"id-{username}",
        username=username,
        password_hash="$argon2id$h",
        role=Role.OPERATOR,
        spring_user_id=7,
        created_at="2026-06-13T00:00:00+00:00",
    )


async def test_user_store_create_and_lookup():
    store = InMemoryUserStore()
    await store.create(_user("alice"))
    fetched = await store.get_by_username("alice")
    assert fetched is not None and fetched.user_id == "id-alice"
    by_id = await store.get_by_id("id-alice")
    assert by_id is not None and by_id.username == "alice"
    assert await store.get_by_username("missing") is None


async def test_user_store_rejects_duplicate_username():
    store = InMemoryUserStore()
    await store.create(_user("alice"))
    with pytest.raises(ValueError):
        await store.create(_user("alice"))


async def test_login_session_create_get_delete():
    store = InMemoryLoginSessionStore()
    session_id = await store.create("id-alice", ttl_seconds=3600)
    record = await store.get(session_id)
    assert record is not None and record["user_id"] == "id-alice"
    await store.delete(session_id)
    assert await store.get(session_id) is None


async def test_login_session_ids_are_distinct():
    store = InMemoryLoginSessionStore()
    first = await store.create("id-alice", ttl_seconds=3600)
    second = await store.create("id-alice", ttl_seconds=3600)
    assert first != second


async def test_login_session_expired_returns_none():
    store = InMemoryLoginSessionStore(now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    session_id = await store.create("id-alice", ttl_seconds=10)
    store.now = lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=11)
    assert await store.get(session_id) is None
