import pytest

from ecommerce_agent.sessions.store import InMemorySessionStore


@pytest.mark.asyncio
async def test_create_get_exists_and_title() -> None:
    store = InMemorySessionStore()
    assert await store.exists("s1") is False

    await store.create("s1")
    assert await store.exists("s1") is True
    record = await store.get("s1")
    assert record is not None
    assert record["session_id"] == "s1"
    assert record["title"] is None
    assert isinstance(record["created_at"], str)


@pytest.mark.asyncio
async def test_set_title_if_absent_only_sets_once() -> None:
    store = InMemorySessionStore()
    await store.create("s1")
    await store.set_title_if_absent("s1", "first")
    await store.set_title_if_absent("s1", "second")
    record = await store.get("s1")
    assert record is not None
    assert record["title"] == "first"


@pytest.mark.asyncio
async def test_list_records_newest_first() -> None:
    store = InMemorySessionStore()
    await store.create("old")
    await store.create("new")
    ids = [record["session_id"] for record in await store.list_records()]
    assert ids == ["new", "old"]
