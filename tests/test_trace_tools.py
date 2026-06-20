from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from ecommerce_agent.trace.tools import (
    DATA_BEARING_TOOLS,
    EXECUTE_TOOL,
    GET_STATISTICS_TOOL,
    fired_tools,
    is_data_bearing,
    sandbox_evidence_fired,
)


def _rec(*events: TraceEvent) -> TraceRecord:
    return TraceRecord(events=list(events))


def _start(name: str) -> TraceEvent:
    return TraceEvent(event_type="tool_call", name=name, phase="start")


def test_fired_tools_dedupes_first_seen() -> None:
    rec = _rec(_start("order_query"), _start("get_statistics"), _start("order_query"))

    assert fired_tools(rec) == ["order_query", "get_statistics"]


def test_data_bearing_allowlist() -> None:
    assert GET_STATISTICS_TOOL in DATA_BEARING_TOOLS
    assert is_data_bearing(GET_STATISTICS_TOOL)
    assert is_data_bearing("order_query")
    assert is_data_bearing(EXECUTE_TOOL)
    assert not is_data_bearing("write_file")
    assert not is_data_bearing("create_chart_spec")
    assert not is_data_bearing("request_approval")


def test_sandbox_evidence_fired_needs_execute_end_with_output() -> None:
    assert not sandbox_evidence_fired(_rec(_start("execute")))
    assert not sandbox_evidence_fired(
        _rec(TraceEvent(event_type="tool_call", name="execute", phase="end"))
    )
    assert sandbox_evidence_fired(
        _rec(
            TraceEvent(
                event_type="tool_call",
                name="execute",
                phase="end",
                result_summary="forecast=1250",
            )
        )
    )
