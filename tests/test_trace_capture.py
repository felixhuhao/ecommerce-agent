from collections.abc import AsyncIterator
from types import SimpleNamespace

from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord


async def _fake_raw_events() -> AsyncIterator[dict]:
    yield {
        "event": "on_tool_start",
        "name": "order_query",
        "run_id": "r1",
        "data": {"input": {"days": 180}},
    }
    yield {
        "event": "on_chat_model_stream",
        "run_id": "r1",
        "data": {"chunk": SimpleNamespace(content="Sales ")},
    }
    yield {
        "event": "on_chat_model_stream",
        "run_id": "r1",
        "data": {"chunk": SimpleNamespace(content="up.")},
    }
    yield {
        "event": "on_tool_end",
        "name": "order_query",
        "run_id": "r1",
        "data": {"output": "[...]"},
    }
    yield {"event": "on_chain_start", "name": "ignored", "data": {}}


async def test_capture_records_tools_and_accumulates_answer() -> None:
    record = TraceRecord()

    yielded = [event async for event in capture(_fake_raw_events(), record)]

    assert [event.event_type for event in yielded] == [
        "tool_call",
        "answer_chunk",
        "answer_chunk",
        "tool_call",
    ]
    assert [event.event_type for event in record.events] == ["tool_call", "tool_call"]
    assert record.tool_names() == ["order_query"]
    assert record.answer == "Sales up."
    assert record.events[0].args_summary is not None
