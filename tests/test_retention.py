from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.mongo import MongoThreadStore
from ecommerce_agent.threads.store import InMemoryThreadStore
from ecommerce_agent.trace.mongo import MongoTraceStore
from ecommerce_agent.trace.schema import TraceRecord
from ecommerce_agent.trace.store import InMemoryTraceStore


class FakeCounters:
    async def find_one_and_update(self, *args, **kwargs) -> dict:
        return {"seq": 1}


class FakeMessages:
    def __init__(self) -> None:
        self.inserted: list[dict] = []
        self.indexes: list[tuple[tuple, dict]] = []

    async def insert_one(self, doc: dict) -> None:
        self.inserted.append(doc)

    async def create_index(self, *args, **kwargs) -> None:
        self.indexes.append((args, kwargs))


class FakeTraces:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.indexes: list[tuple[tuple, dict]] = []

    async def create_index(self, *args, **kwargs) -> None:
        self.indexes.append((args, kwargs))

    async def update_one(self, filt: dict, update: dict, upsert: bool = False) -> None:
        self.docs[(filt["session_id"], filt["turn_id"])] = {"_id": "oid", **update["$set"]}

    async def find_one(self, filt: dict) -> dict | None:
        return self.docs.get((filt["session_id"], filt["turn_id"]))


async def test_thread_append_sets_expire_at_in_the_future() -> None:
    messages = FakeMessages()
    store = MongoThreadStore(messages=messages, counters=FakeCounters(), retention_days=90)

    await store.append(ThreadMessage(session_id="s1", type="user", content="hi"))

    doc = messages.inserted[0]
    assert isinstance(doc["expire_at"], datetime)
    assert doc["expire_at"] > datetime.now(UTC)


async def test_thread_ensure_indexes_creates_ttl_index() -> None:
    messages = FakeMessages()
    store = MongoThreadStore(messages=messages, counters=FakeCounters(), retention_days=90)

    await store.ensure_indexes()

    ttl = [
        (args, kwargs)
        for args, kwargs in messages.indexes
        if kwargs == {"expireAfterSeconds": 0}
    ]
    assert any(args == ("expire_at",) for args, _ in ttl)


async def test_in_memory_thread_sweep_removes_expired_messages() -> None:
    now = datetime(2026, 6, 13, tzinfo=UTC)
    store = InMemoryThreadStore(retention_days=1, now=lambda: now)
    await store.append(
        ThreadMessage(
            session_id="s1",
            type="user",
            content="old",
            created_at=(now - timedelta(days=2)).isoformat(),
        )
    )
    await store.append(
        ThreadMessage(
            session_id="s1",
            type="user",
            content="fresh",
            created_at=now.isoformat(),
        )
    )

    assert await store.sweep_expired() == 1
    assert [message.content for message in await store.list_messages("s1")] == ["fresh"]


async def test_trace_save_sets_expire_at_in_the_future() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, retention_days=90)

    await store.save(TraceRecord(session_id="s1", turn_id="t1"))

    doc = traces.docs[("s1", "t1")]
    assert isinstance(doc["expire_at"], datetime)
    assert doc["expire_at"] > datetime.now(UTC)


async def test_trace_ensure_indexes_creates_ttl_index() -> None:
    traces = FakeTraces()
    store = MongoTraceStore(traces=traces, retention_days=90)

    await store.ensure_indexes()

    ttl = [
        (args, kwargs)
        for args, kwargs in traces.indexes
        if kwargs == {"expireAfterSeconds": 0}
    ]
    assert any(args == ("expire_at",) for args, _ in ttl)


async def test_in_memory_trace_sweep_removes_expired_records() -> None:
    now = datetime(2026, 6, 13, tzinfo=UTC)
    store = InMemoryTraceStore(retention_days=1, now=lambda: now)
    old = TraceRecord(session_id="s1", turn_id="old")
    old.started_at = (now - timedelta(days=2)).timestamp()
    fresh = TraceRecord(session_id="s1", turn_id="fresh")
    fresh.started_at = now.timestamp()
    await store.save(old)
    await store.save(fresh)

    assert await store.sweep_expired() == 1
    assert await store.get("s1", "old") is None
    assert await store.get("s1", "fresh") is not None
