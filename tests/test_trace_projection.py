from ecommerce_agent.trace.projection import project_timeline
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def _record_with_spans() -> TraceRecord:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events += [
        TraceEvent(
            event_type="model_call",
            name="chat",
            phase="start",
            model_call_id="m1",
            args_summary="prompt",
            ts=1.0,
        ),
        TraceEvent(
            event_type="model_call",
            name="chat",
            phase="end",
            model_call_id="m1",
            result_summary="resp",
            duration_ms=50.0,
            tokens_in=10,
            tokens_out=20,
            ts=1.05,
        ),
        TraceEvent(
            event_type="tool_call",
            name="generate_line_chart",
            phase="start",
            tool_call_id="x1",
            args_summary="series",
            ts=2.0,
        ),
        TraceEvent(
            event_type="tool_call",
            name="generate_line_chart",
            phase="end",
            tool_call_id="x1",
            result_summary="data:image/...",
            duration_ms=12.0,
            artifact_id="chart-x1",
            artifact={"id": "chart-x1", "src": "data:image/svg+xml,<svg/>"},
            ts=2.01,
        ),
        TraceEvent(
            event_type="tool_call",
            name="get_statistics",
            phase="start",
            tool_call_id="g1",
            args_summary='{"metric":"sales"}',
            ts=3.0,
        ),
        TraceEvent(
            event_type="tool_call",
            name="get_statistics",
            phase="end",
            tool_call_id="g1",
            result_summary="sales rows",
            evidence="full sales evidence",
            duration_ms=20.0,
            ts=3.02,
        ),
    ]
    record.finish()
    return record


def test_project_timeline_merges_spans_and_drops_artifact_src() -> None:
    timeline = project_timeline(_record_with_spans())

    assert timeline["turn_id"] == "t1"
    assert timeline["span_count"] == 3
    assert timeline["tokens_in_total"] == 10
    assert timeline["tokens_out_total"] == 20

    model, tool, data_tool = timeline["spans"]
    assert model["kind"] == "model_call"
    assert model["args_summary"] == "prompt"
    assert model["result_summary"] == "resp"
    assert model["duration_ms"] == 50.0

    assert tool["kind"] == "tool_call"
    assert tool["name"] == "generate_line_chart"
    assert tool["args_summary"] == "series"
    assert tool["duration_ms"] == 12.0
    assert tool["artifact_id"] == "chart-x1"
    assert "artifact" not in tool
    assert "src" not in tool

    assert data_tool["name"] == "get_statistics"
    assert data_tool["evidence"] == "full sales evidence"


def test_project_timeline_orders_by_ts_and_handles_start_only() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events += [
        TraceEvent(event_type="tool_call", name="late", phase="start", tool_call_id="b", ts=5.0),
        TraceEvent(event_type="tool_call", name="early", phase="start", tool_call_id="a", ts=1.0),
    ]

    timeline = project_timeline(record)

    assert [span["name"] for span in timeline["spans"]] == ["early", "late"]
    assert timeline["spans"][0]["duration_ms"] is None
    assert timeline["tokens_in_total"] is None


def test_project_timeline_ignores_answer_chunk_and_unknown_events() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="answer_chunk", result_summary="Hi", ts=1.0))

    timeline = project_timeline(record)

    assert timeline["spans"] == []
    assert timeline["span_count"] == 0


def test_project_timeline_includes_route_decision() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(
        TraceEvent(
            event_type="route_decision",
            name="order-manager",
            phase="end",
            status="ok",
            result_summary="classifier: po",
            ts=0.5,
        )
    )

    timeline = project_timeline(record)

    assert timeline["span_count"] == 1
    span = timeline["spans"][0]
    assert span["kind"] == "route_decision"
    assert span["name"] == "order-manager"
    assert span["result_summary"] == "classifier: po"
