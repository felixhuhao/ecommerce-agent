import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.live_reliability import assess_attempt, run_reliability
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


def test_assess_attempt_passes_on_good_trace() -> None:
    record = _record_with_tools("order_query", "execute", "generate_visualization")

    result = assess_attempt(record, "event: tool\nevent: done\n")

    assert result.passed, result.failures


def test_assess_attempt_flags_write_tool_and_missing_done() -> None:
    record = _record_with_tools("order_query", "purchase_order_create")

    result = assess_attempt(record, "event: error\n")

    assert not result.passed
    assert any("write/approval" in failure for failure in result.failures)
    assert any("did not complete" in failure for failure in result.failures)


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
    print("reliability:", report["passed"], "/", report["n"], report["failure_modes"])
