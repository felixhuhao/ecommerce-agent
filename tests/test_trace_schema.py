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


def test_trace_record_from_dict_round_trip_rebuilds_events() -> None:
    record = TraceRecord(session_id="s1", turn_id="t1")
    record.events.append(
        TraceEvent(event_type="tool_call", name="order_query", phase="end", duration_ms=12.0)
    )
    record.finish()

    restored = TraceRecord.from_dict(record.to_dict())

    assert isinstance(restored, TraceRecord)
    assert restored.session_id == "s1"
    assert restored.turn_id == "t1"
    assert restored.duration_ms == record.duration_ms
    assert len(restored.events) == 1
    assert isinstance(restored.events[0], TraceEvent)
    assert restored.events[0].name == "order_query"
    assert restored.events[0].duration_ms == 12.0


def test_trace_record_from_dict_ignores_unknown_keys() -> None:
    data = TraceRecord(session_id="s1", turn_id="t1").to_dict()
    data["_id"] = "mongo-oid"
    data["unexpected"] = 1
    data["events"] = []

    restored = TraceRecord.from_dict(data)

    assert restored.session_id == "s1"
    assert not hasattr(restored, "_id")
