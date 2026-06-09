import json

from ecommerce_agent.trace.jsonl import append_eval_baseline, dump_trace
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def test_dump_trace_appends_one_json_line(tmp_path) -> None:
    path = tmp_path / "traces" / "trace.jsonl"
    record = TraceRecord(session_id="s1")
    record.events.append(TraceEvent(event_type="tool_call", name="order_query", phase="start"))
    record.finish()

    dump_trace(record, str(path))
    dump_trace(record, str(path))

    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["session_id"] == "s1"
    assert parsed["events"][0]["name"] == "order_query"


def test_append_eval_baseline_appends_record(tmp_path) -> None:
    path = tmp_path / "evals" / "baseline.jsonl"

    append_eval_baseline({"n": 5, "pass_rate": 0.8}, str(path))

    parsed = json.loads(path.read_text().strip())
    assert parsed["pass_rate"] == 0.8
