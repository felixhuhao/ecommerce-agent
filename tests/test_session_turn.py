from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.store import InMemoryThreadStore


class FakeAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str) -> AsyncIterator[dict]:
        assert inputs["messages"][0]["content"] == "hello"
        assert version == "v2"
        yield {"event": "on_tool_start", "name": "inventory_query", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Inventory looks healthy.")},
        }
        yield {"event": "on_tool_end", "name": "inventory_query", "data": {}}


@pytest.mark.asyncio
async def test_run_turn_publishes_events_and_appends_answer() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()
    seen: list[dict] = []

    async with bus.subscription("s1") as sub:
        await run_turn(
            agent=FakeAgent(),
            message="hello",
            session_id="s1",
            turn_id="t1",
            store=store,
            bus=bus,
            recursion_limit=80,
        )
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())

    kinds = [event["event"] for event in seen]
    assert "tool" in kinds
    assert "token" in kinds
    assert "thread.append" in kinds
    assert kinds[-1] == "done"

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].content == "Inventory looks healthy."
    assert messages[0].turn_id == "t1"
    assert messages[0].actor_id == "agent"


@pytest.mark.asyncio
async def test_run_turn_failure_appends_durable_agent_answer() -> None:
    class ExplodingAgent:
        async def astream_events(
            self,
            inputs: dict,
            config: dict,
            version: str,
        ) -> AsyncIterator[dict]:
            raise RuntimeError("boom")
            yield

    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ExplodingAgent(),
        message="hi",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
    )

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].status == "failed"
