import asyncio

import pytest

from ecommerce_agent.sessions.bus import SessionBus


@pytest.mark.asyncio
async def test_subscribe_buffers_events_published_after_open() -> None:
    bus = SessionBus()

    async with bus.subscription("s1") as sub:
        bus.publish("s1", {"event": "token", "text": "hi"})
        bus.publish("s1", {"event": "done"})

        first = await asyncio.wait_for(sub.queue.get(), timeout=1)
        second = await asyncio.wait_for(sub.queue.get(), timeout=1)

    assert first == {"event": "token", "text": "hi"}
    assert second == {"event": "done"}


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers_and_cleans_up() -> None:
    bus = SessionBus()

    async with bus.subscription("s1") as a, bus.subscription("s1") as b:
        bus.publish("s1", {"event": "x"})
        assert (await asyncio.wait_for(a.queue.get(), timeout=1)) == {"event": "x"}
        assert (await asyncio.wait_for(b.queue.get(), timeout=1)) == {"event": "x"}

    bus.publish("s1", {"event": "y"})
    assert bus.subscriber_count("s1") == 0


@pytest.mark.asyncio
async def test_publish_to_session_without_subscribers_is_noop() -> None:
    bus = SessionBus()
    bus.publish("nobody", {"event": "x"})
    assert bus.subscriber_count("nobody") == 0
