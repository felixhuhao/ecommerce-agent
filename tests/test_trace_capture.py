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


async def test_capture_records_model_call_timing_and_tokens() -> None:
    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_chat_model_start",
            "name": "ChatDeepSeek",
            "run_id": "model-run",
            "data": {"input": {"messages": ["hi"]}},
        }
        yield {
            "event": "on_chat_model_end",
            "name": "ChatDeepSeek",
            "run_id": "model-run",
            "data": {
                "output": SimpleNamespace(
                    usage_metadata={"input_tokens": 123, "output_tokens": 45}
                )
            },
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert [event.event_type for event in yielded] == ["model_call", "model_call"]
    assert yielded[0].phase == "start"
    assert yielded[1].phase == "end"
    assert yielded[1].duration_ms is not None
    assert yielded[1].tokens_in == 123
    assert yielded[1].tokens_out == 45
    assert record.events == yielded


async def test_capture_extracts_model_tokens_from_response_metadata() -> None:
    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_chat_model_end",
            "name": "ChatDeepSeek",
            "run_id": "model-run",
            "data": {
                "output": SimpleNamespace(
                    response_metadata={
                        "token_usage": {"prompt_tokens": 12, "completion_tokens": 7}
                    }
                )
            },
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert yielded[0].tokens_in == 12
    assert yielded[0].tokens_out == 7


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


async def test_capture_extracts_approval_id_from_wrapped_tool_message() -> None:
    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_end",
            "name": "request_approval",
            "run_id": "approval-run",
            "data": {
                "output": SimpleNamespace(
                    content=[
                        {
                            "type": "text",
                            "text": '{"approvalId":"approval-wrapped","status":"pending"}',
                        }
                    ]
                )
            },
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert yielded[0].approval_id == "approval-wrapped"
    assert record.events[0].approval_id == "approval-wrapped"


async def test_capture_extracts_chart_artifact_from_modelscope_output() -> None:
    image_src = "data:image/svg+xml;base64,PHN2Zy8+"

    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_end",
            "name": "generate_line_chart",
            "run_id": "chart-run",
            "data": {"output": [{"type": "text", "text": image_src, "id": "chart-1"}]},
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert yielded[0].artifact_id == "chart-1"
    assert yielded[0].artifact == {
        "id": "chart-1",
        "kind": "image",
        "mime_type": "image/svg+xml",
        "src": image_src,
    }
    assert record.events[0].artifact == yielded[0].artifact


async def test_capture_extracts_chart_artifact_from_wrapped_tool_message() -> None:
    image_src = "data:image/svg+xml;base64,PHN2Zy8+"

    async def raw_events() -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_end",
            "name": "generate_line_chart",
            "run_id": "chart-run",
            "data": {
                "output": SimpleNamespace(
                    content=[{"type": "text", "text": image_src, "id": "chart-1"}]
                )
            },
        }

    record = TraceRecord()

    yielded = [event async for event in capture(raw_events(), record)]

    assert yielded[0].artifact_id == "chart-1"
    assert yielded[0].artifact["src"] == image_src
