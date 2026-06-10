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


async def test_capture_records_tools_and_tracks_current_model_answer() -> None:
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


async def test_capture_keeps_only_latest_model_run_as_final_answer() -> None:
    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_chat_model_stream",
            "run_id": "planning",
            "data": {"chunk": SimpleNamespace(content="I will try a few approaches.")},
        }
        yield {
            "event": "on_tool_start",
            "name": "order_query",
            "run_id": "tool-run",
            "data": {"input": {"limit": 100}},
        }
        yield {
            "event": "on_tool_end",
            "name": "order_query",
            "run_id": "tool-run",
            "data": {"output": "[...]"},
        }
        yield {
            "event": "on_chat_model_stream",
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Final answer only.")},
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert [event.event_type for event in yielded] == [
        "answer_chunk",
        "tool_call",
        "tool_call",
        "answer_chunk",
    ]
    assert record.answer == "Final answer only."
    assert "try a few approaches" not in record.answer


async def test_capture_extracts_approval_id_before_summarizing_output() -> None:
    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_end",
            "name": "request_approval",
            "run_id": "approval-run",
            "data": {
                "output": {
                    "approvalId": "approval-1",
                    "operationDetail": "x" * 1000,
                }
            },
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert yielded[0].approval_id == "approval-1"
    assert record.events[0].approval_id == "approval-1"
    assert record.events[0].result_summary is not None
    assert len(record.events[0].result_summary) < 600
