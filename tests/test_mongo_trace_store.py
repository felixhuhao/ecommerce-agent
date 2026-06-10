import pytest

from ecommerce_agent.trace.mongo import MongoTraceStore
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


class FakeTraces:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.indexes: list[tuple] = []

    async def create_index(self, keys, unique=False):  # noqa: ANN001
        self.indexes.append((tuple(keys), unique))

    async def update_one(self, filt, update, upsert=False):  # noqa: ANN001
        key = (filt["session_id"], filt["turn_id"])
        self.docs[key] = {"_id": "oid", **update["$set"]}

    async def find_one(self, filt):  # noqa: ANN001
        return self.docs.get((filt["session_id"], filt["turn_id"]))


class FakeClient:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_save_upserts_by_turn_and_get_reconstructs_record() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, client=FakeClient())

    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    await store.save(record)
    rerun = TraceRecord(session_id="s1", turn_id="t1", answer="redo")
    rerun.events.append(TraceEvent(event_type="tool_call", name="get_statistics", phase="end"))
    await store.save(rerun)

    assert len(traces.docs) == 1
    got = await store.get("s1", "t1")
    assert got is not None
    assert isinstance(got.events[0], TraceEvent)
    assert got.events[0].name == "get_statistics"
    assert got.answer == "redo"
    assert await store.get("s1", "missing") is None


@pytest.mark.asyncio
async def test_first_save_creates_unique_compound_index_once() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, client=FakeClient())

    await store.save(TraceRecord(session_id="s1", turn_id="t1"))
    await store.save(TraceRecord(session_id="s1", turn_id="t2"))

    assert traces.indexes == [((("session_id", 1), ("turn_id", 1)), True)]


def test_close_closes_client() -> None:
    client = FakeClient()
    MongoTraceStore(traces=FakeTraces(), client=client).close()
    assert client.closed is True
