from collections.abc import AsyncIterator

from ecommerce_agent.trace.capture import capture
from ecommerce_agent.trace.schema import TraceRecord


async def _run(raw_events: list[dict], **kwargs):
    record = TraceRecord()

    async def gen() -> AsyncIterator[dict]:
        for event in raw_events:
            yield event

    out = [event async for event in capture(gen(), record, **kwargs)]
    return record, out


async def test_evidence_captured_for_data_bearing_tool_and_capped() -> None:
    big = "x" * 5000
    raw = [
        {"event": "on_tool_start", "name": "get_statistics", "run_id": "r1", "data": {}},
        {
            "event": "on_tool_end",
            "name": "get_statistics",
            "run_id": "r1",
            "data": {"output": big},
        },
    ]

    record, _ = await _run(raw, evidence_max_chars=2000)

    end = next(event for event in record.events if event.phase == "end")
    assert end.evidence is not None and len(end.evidence) == 2000
    assert end.result_summary is not None and len(end.result_summary) <= 503


async def test_no_evidence_for_non_data_bearing_tool() -> None:
    raw = [
        {"event": "on_tool_start", "name": "write_file", "run_id": "r2", "data": {}},
        {
            "event": "on_tool_end",
            "name": "write_file",
            "run_id": "r2",
            "data": {"output": "ok"},
        },
    ]

    record, _ = await _run(raw, evidence_max_chars=2000)

    end = next(event for event in record.events if event.phase == "end")
    assert end.evidence is None
