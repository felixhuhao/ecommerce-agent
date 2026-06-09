from ecommerce_agent.trace.schema import SCHEMA_VERSION, TraceEvent, TraceRecord


def test_trace_record_finish_sets_duration_and_tool_names() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="start"))
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="end"))
    record.events.append(TraceEvent(event_type="tool_call", name="get_statistics", phase="start"))

    record.finish()

    assert record.schema_version == SCHEMA_VERSION
    assert record.duration_ms is not None and record.duration_ms >= 0
    assert record.tool_names() == ["order_query", "get_statistics"]


def test_trace_event_to_dict_is_json_native() -> None:
    event = TraceEvent(event_type="tool_call", name="x", phase="start")

    data = event.to_dict()

    assert data["event_type"] == "tool_call"
    assert data["name"] == "x"
    assert "span_id" in data
    assert "trace_id" in data
