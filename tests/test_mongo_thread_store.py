import pytest

from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.mongo import MongoThreadStore


class FakeCounters:
    def __init__(self) -> None:
        self._seqs: dict[str, int] = {}

    async def find_one_and_update(self, filt, update, upsert, return_document):  # noqa: ANN001
        sid = filt["_id"]
        self._seqs[sid] = self._seqs.get(sid, 0) + 1
        return {"_id": sid, "seq": self._seqs[sid]}


class FakeCursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def sort(self, key, direction):  # noqa: ANN001
        self._docs.sort(key=lambda doc: doc[key])
        return self

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc

        return gen()


class FakeMessages:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc):  # noqa: ANN001
        self.docs.append(doc)

    def find(self, filt):  # noqa: ANN001
        return FakeCursor(
            [dict(doc) for doc in self.docs if doc["session_id"] == filt["session_id"]]
        )


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_mongo_store_append_and_list() -> None:
    store = MongoThreadStore(messages=FakeMessages(), counters=FakeCounters())

    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    msgs = await store.list_messages("s1")
    assert [msg.seq for msg in msgs] == [1, 2]
    assert [msg.content for msg in msgs] == ["a", "b"]


def test_mongo_store_closes_owned_client() -> None:
    client = FakeClient()
    store = MongoThreadStore(messages=FakeMessages(), counters=FakeCounters(), client=client)

    store.close()

    assert client.closed is True
