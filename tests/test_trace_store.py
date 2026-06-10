import pytest

from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from ecommerce_agent.trace.store import InMemoryTraceStore


@pytest.mark.asyncio
async def test_save_and_get_round_trip() -> None:
    store = InMemoryTraceStore()
    assert await store.get("s1", "t1") is None

    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    await store.save(record)

    got = await store.get("s1", "t1")
    assert got is not None
    assert got.turn_id == "t1"
    assert got.events[0].name == "order_query"


@pytest.mark.asyncio
async def test_resave_same_turn_keeps_one_record() -> None:
    store = InMemoryTraceStore()
    await store.save(TraceRecord(session_id="s1", turn_id="t1", answer="first"))
    await store.save(TraceRecord(session_id="s1", turn_id="t1", answer="second"))

    got = await store.get("s1", "t1")
    assert got is not None and got.answer == "second"


@pytest.mark.asyncio
async def test_ping_is_true_for_in_memory() -> None:
    assert await InMemoryTraceStore().ping() is True
