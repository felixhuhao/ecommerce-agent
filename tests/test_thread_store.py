import pytest

from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import InMemoryThreadStore


@pytest.mark.asyncio
async def test_append_assigns_monotonic_seq_per_session() -> None:
    store = InMemoryThreadStore()

    a = await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    b = await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))
    other = await store.append(ThreadMessage(session_id="s2", type="user", content="c"))

    assert a.seq == 1
    assert b.seq == 2
    assert other.seq == 1


@pytest.mark.asyncio
async def test_list_messages_returns_seq_ordered_copy() -> None:
    store = InMemoryThreadStore()
    await store.append(ThreadMessage(session_id="s1", type="user", content="a"))
    await store.append(ThreadMessage(session_id="s1", type="agent_answer", content="b"))

    msgs = await store.list_messages("s1")

    assert [m.seq for m in msgs] == [1, 2]
    assert [m.content for m in msgs] == ["a", "b"]
    assert await store.list_messages("missing") == []
