import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.sessions.store import MongoSessionStore


class FakeSessions:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}
        self._order: list[str] = []

    async def update_one(self, filt, update, upsert=False):  # noqa: ANN001
        session_id = filt["_id"]
        if "$setOnInsert" in update:
            if session_id not in self.docs:
                self.docs[session_id] = {"_id": session_id, **update["$setOnInsert"]}
                self._order.append(session_id)
            return
        if "$set" in update:
            doc = self.docs.get(session_id)
            if doc is not None and all(doc.get(key) == value for key, value in filt.items()):
                doc.update(update["$set"])

    async def count_documents(self, filt, limit=None):  # noqa: ANN001
        return 1 if filt["_id"] in self.docs else 0

    async def find_one(self, filt):  # noqa: ANN001
        return self.docs.get(filt["_id"])

    def find(self):
        order = self._order
        docs = self.docs

        class _Cursor:
            def sort(self, key, direction):  # noqa: ANN001
                return self

            def __aiter__(self_inner):
                async def gen():
                    for session_id in reversed(order):
                        yield docs[session_id]

                return gen()

        return _Cursor()


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_mongo_session_store_create_title_list_and_close() -> None:
    client = FakeClient()
    store = MongoSessionStore(sessions=FakeSessions(), client=client)
    await store.create("s1")
    await store.create("s1")
    assert await store.exists("s1") is True
    await store.set_title_if_absent("s1", "hello")
    await store.set_title_if_absent("s1", "ignored")
    record = await store.get("s1")
    assert record is not None
    assert record["title"] == "hello"
    await store.create("s2")
    assert [item["session_id"] for item in await store.list_records()] == ["s2", "s1"]

    store.close()
    assert client.closed is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_mongo_session_store() -> None:
    if not os.environ.get("RUN_MONGO_INTEGRATION"):
        pytest.skip("set RUN_MONGO_INTEGRATION and run a local Mongo to exercise this")
    store = MongoSessionStore.from_settings(Settings(_env_file=None))
    try:
        session_id = f"itest-{os.getpid()}"
        await store.create(session_id)
        assert await store.exists(session_id) is True
    finally:
        store.close()
