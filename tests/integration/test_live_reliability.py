import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals import live_reliability
from ecommerce_agent.evals.live_reliability import assess_attempt, run_reliability
from ecommerce_agent.mcp_client import VIZ_TOOLS
from ecommerce_agent.trace.jsonl import append_eval_baseline
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord
from tests.integration.helpers import (
    skip_unless_docker_available,
    skip_unless_spring_mcp_is_running,
)

_LIVE_BATCH_TIMEOUT_SECONDS = 420


@contextmanager
def _fail_after(seconds: int) -> Iterator[None]:
    def raise_timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"live reliability batch exceeded {seconds}s")

    previous_handler = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _record_with_tools(*names: str) -> TraceRecord:
    record = TraceRecord()
    for name in names:
        record.events.append(TraceEvent(event_type="tool_call", name=name, phase="start"))
    return record


@pytest.mark.parametrize("viz_tool", sorted(VIZ_TOOLS))
def test_assess_attempt_passes_on_good_trace(viz_tool: str) -> None:
    record = _record_with_tools("order_query", "execute", viz_tool)

    result = assess_attempt(record, "event: tool\nevent: done\n")

    assert result.passed, result.failures


def test_assess_attempt_can_require_visualization_tool() -> None:
    record = _record_with_tools("order_query", "execute")

    result = assess_attempt(record, "event: tool\nevent: done\n", require_viz=True)

    assert not result.passed
    assert "visualization tool not called" in result.failures


def test_assess_attempt_flags_write_tool_and_missing_done() -> None:
    record = _record_with_tools("order_query", "purchase_order_create")

    result = assess_attempt(record, "event: error\n")

    assert not result.passed
    assert any("write/approval" in failure for failure in result.failures)
    assert any("did not complete" in failure for failure in result.failures)
    assert result.body_tail == "event: error\n"
    assert result.trace_summary is not None
    assert result.trace_summary["tool_names"] == ["order_query", "purchase_order_create"]


def test_run_reliability_records_failed_attempt_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    def fake_run_single_attempt(
        settings: Settings,  # noqa: ARG001
        *,
        prompt: str,  # noqa: ARG001
        attempt_timeout_seconds: int,
        require_viz: bool,
    ) -> tuple[live_reliability.AttemptResult, TraceRecord]:
        assert require_viz is False
        record = _record_with_tools("order_query")
        record.answer = "partial answer"
        result = assess_attempt(record, "event: token\ndata: {}\n")
        result.duration_ms = float(attempt_timeout_seconds)
        return result, record

    trace_path = tmp_path / "failed-traces.jsonl"
    monkeypatch.setattr(live_reliability, "_run_single_attempt", fake_run_single_attempt)

    report = run_reliability(
        1,
        Settings(_env_file=None),
        attempt_timeout_seconds=7,
        failure_trace_path=str(trace_path),
        require_viz=False,
    )

    assert report["passed"] == 0
    assert report["pass_rate"] == 0.0
    assert report["attempt_timeout_seconds"] == 7
    assert report["require_viz"] is False
    assert report["failure_trace_path"] == str(trace_path)
    assert report["attempts"][0]["trace_path"] == str(trace_path)
    assert report["attempts"][0]["body_tail"] == "event: token\ndata: {}\n"
    assert report["attempts"][0]["trace_summary"]["answer_tail"] == "partial answer"
    assert trace_path.read_text(encoding="utf-8")


@pytest.mark.integration
@pytest.mark.live
async def test_live_reliability_batch_records_baseline(tmp_path) -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live reliability batch")
    skip_unless_docker_available()

    settings = Settings(mcp_request_timeout_seconds=15, mcp_sse_read_timeout_seconds=120)
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    import docker

    try:
        docker.from_env().images.get(settings.sandbox_image)
    except Exception:
        pytest.skip(f"sandbox image {settings.sandbox_image} is not built")

    await skip_unless_spring_mcp_is_running(settings)

    runs = int(os.getenv("LIVE_EVAL_RUNS", "5"))
    try:
        with _fail_after(_LIVE_BATCH_TIMEOUT_SECONDS):
            report = run_reliability(runs, settings)
    except TimeoutError as exc:
        pytest.fail(str(exc))

    append_eval_baseline(report, str(tmp_path / "baseline.jsonl"))

    assert report["n"] == runs
    assert 0.0 <= report["pass_rate"] <= 1.0
    assert report["require_viz"] is bool(settings.modelscope_mcp_url)
    print(
        "reliability:",
        report["passed"],
        "/",
        report["n"],
        report["failure_modes"],
        "failure_trace_path:",
        report["failure_trace_path"],
    )
