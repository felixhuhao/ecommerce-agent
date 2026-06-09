"""On-demand N-run reliability harness for the M1 forecast hero."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import VIZ_TOOLS, WRITE_OR_APPROVAL_SPRING_TOOLS
from ecommerce_agent.trace.jsonl import dump_trace
from ecommerce_agent.trace.schema import TraceRecord

_DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 180
_DEFAULT_FAILURE_TRACE_PATH = ".pytest_cache/live-reliability-failures.jsonl"
_TAIL_CHARS = 2000
_LAST_EVENT_COUNT = 8

HERO_PROMPT = (
    "Which categories are trending up or down over the last 6 months, forecast next "
    "month's sales, and chart the result. If product_query does not return a product "
    "ID from an order item, bucket it as unknown and continue. Keep the summary short."
)


class AttemptTimeoutError(TimeoutError):
    """Raised when one live reliability attempt exceeds its budget."""


@dataclass
class AttemptResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    status_code: int | None = None
    duration_ms: float | None = None
    body_tail: str = ""
    trace_summary: dict | None = None
    trace_path: str | None = None
    exception: str | None = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failures": self.failures,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
            "body_tail": self.body_tail,
            "trace_summary": self.trace_summary,
            "trace_path": self.trace_path,
            "exception": self.exception,
        }


def _event_summary(event: object) -> dict:
    return {
        "event_type": getattr(event, "event_type", None),
        "name": getattr(event, "name", None),
        "phase": getattr(event, "phase", None),
        "status": getattr(event, "status", None),
        "args_summary": getattr(event, "args_summary", None),
        "result_summary": getattr(event, "result_summary", None),
        "error_message": getattr(event, "error_message", None),
        "duration_ms": getattr(event, "duration_ms", None),
    }


def summarize_trace(record: TraceRecord) -> dict:
    """Small failure-oriented trace summary for console/baseline output."""
    return {
        "trace_id": record.trace_id,
        "duration_ms": record.duration_ms,
        "tool_names": record.tool_names(),
        "event_count": len(record.events),
        "last_events": [_event_summary(event) for event in record.events[-_LAST_EVENT_COUNT:]],
        "answer_tail": record.answer[-_TAIL_CHARS:],
    }


def assess_attempt(
    record: TraceRecord,
    stream_body: str,
    *,
    require_viz: bool = False,
) -> AttemptResult:
    """Structural pass/fail for one hero attempt. No semantic judgement."""
    failures: list[str] = []
    tools = set(record.tool_names())

    if "order_query" not in tools:
        failures.append("order_query not called")
    leaked = tools & set(WRITE_OR_APPROVAL_SPRING_TOOLS)
    if leaked:
        failures.append(f"write/approval tools appeared: {sorted(leaked)}")
    called_viz_tools = tools & VIZ_TOOLS
    if not ({"execute"} & tools or called_viz_tools):
        failures.append("neither sandbox execute nor visualization tool was called")
    if require_viz and not called_viz_tools:
        failures.append("visualization tool not called")
    if "event: error" in stream_body or "event: done" not in stream_body:
        failures.append("stream did not complete cleanly")

    result = AttemptResult(passed=not failures, failures=failures)
    if failures:
        result.body_tail = stream_body[-_TAIL_CHARS:]
        result.trace_summary = summarize_trace(record)
    return result


@contextmanager
def _attempt_timeout(seconds: int) -> Iterator[None]:
    """Unix-friendly timeout that preserves any outer alarm used by pytest."""
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def raise_timeout(signum, frame):  # noqa: ARG001
        raise AttemptTimeoutError(f"live reliability attempt exceeded {seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_remaining = signal.alarm(0)
    started = time.monotonic()
    signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_remaining:
            elapsed = int(time.monotonic() - started)
            signal.alarm(max(1, previous_remaining - elapsed))


def _failure_result(
    *,
    failure: str,
    record: TraceRecord,
    body: str = "",
    started_at: float,
    status_code: int | None = None,
    exception: BaseException | None = None,
) -> AttemptResult:
    if record.ended_at is None:
        record.finish()
    result = AttemptResult(
        passed=False,
        failures=[failure],
        status_code=status_code,
        duration_ms=(time.monotonic() - started_at) * 1000.0,
        body_tail=body[-_TAIL_CHARS:],
        trace_summary=summarize_trace(record),
        exception=f"{type(exception).__name__}: {exception}" if exception else None,
    )
    return result


def _prompt_hash() -> str:
    from ecommerce_agent.prompts.loader import get_prompt

    return hashlib.sha256(get_prompt("sales_analyst").encode("utf-8")).hexdigest()[:16]


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("deepagents", "langgraph", "langchain-mcp-adapters", "langchain-openai"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            check=False,
            text=True,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _run_metadata(settings: Settings) -> dict:
    return {
        "git_commit": _git_commit(),
        "prompt_hash": _prompt_hash(),
        "dependency_versions": _dependency_versions(),
        "model": {
            "name": settings.llm_model,
            "base_url": settings.llm_base_url,
            "temperature": settings.llm_temperature,
        },
    }


def _close_sandbox(app: object) -> None:
    backend = getattr(getattr(app, "state", None), "sandbox_backend", None)
    close = getattr(backend, "close", None)
    if callable(close):
        close()


def _run_single_attempt(
    settings: Settings,
    *,
    prompt: str,
    attempt_timeout_seconds: int,
    require_viz: bool,
) -> tuple[AttemptResult, TraceRecord]:
    from fastapi.testclient import TestClient

    from ecommerce_agent.api.app import create_app

    app = create_app(settings=settings)
    body = ""
    status_code: int | None = None
    started_at = time.monotonic()
    record = TraceRecord()
    try:
        with _attempt_timeout(attempt_timeout_seconds), TestClient(app) as client:
            with client.stream("POST", "/api/chat/stream", json={"message": prompt}) as response:
                status_code = response.status_code
                body = "".join(response.iter_text())
            record = app.state.last_trace or TraceRecord()
            result = assess_attempt(record, body, require_viz=require_viz)
            result.status_code = status_code
            result.duration_ms = (time.monotonic() - started_at) * 1000.0
            if status_code != 200:
                result.passed = False
                result.failures.append(f"unexpected status code: {status_code}")
                result.body_tail = body[-_TAIL_CHARS:]
                result.trace_summary = summarize_trace(record)
            return result, record
    except AttemptTimeoutError as exc:
        record = getattr(app.state, "last_trace", None) or record
        return (
            _failure_result(
                failure=f"attempt timed out after {attempt_timeout_seconds}s",
                record=record,
                body=body,
                started_at=started_at,
                status_code=status_code,
                exception=exc,
            ),
            record,
        )
    except Exception as exc:
        record = getattr(app.state, "last_trace", None) or record
        return (
            _failure_result(
                failure="attempt raised exception",
                record=record,
                body=body,
                started_at=started_at,
                status_code=status_code,
                exception=exc,
            ),
            record,
        )
    finally:
        _close_sandbox(app)


def _default_attempt_timeout_seconds() -> int:
    raw = os.getenv("LIVE_EVAL_ATTEMPT_TIMEOUT_SECONDS")
    return int(raw) if raw else _DEFAULT_ATTEMPT_TIMEOUT_SECONDS


def _default_failure_trace_path() -> str:
    return os.getenv("LIVE_EVAL_FAILURE_TRACE_PATH", _DEFAULT_FAILURE_TRACE_PATH)


def run_reliability(
    n: int,
    settings: Settings,
    *,
    prompt: str = HERO_PROMPT,
    attempt_timeout_seconds: int | None = None,
    failure_trace_path: str | None = None,
    require_viz: bool | None = None,
) -> dict:
    """Run the hero prompt `n` times and return a diagnostic batch report."""
    timeout = attempt_timeout_seconds or _default_attempt_timeout_seconds()
    trace_path = failure_trace_path or _default_failure_trace_path()
    require_viz_tool = bool(settings.modelscope_mcp_url) if require_viz is None else require_viz

    attempts: list[AttemptResult] = []
    for _ in range(n):
        result, record = _run_single_attempt(
            settings,
            prompt=prompt,
            attempt_timeout_seconds=timeout,
            require_viz=require_viz_tool,
        )
        if not result.passed:
            dump_trace(record, trace_path)
            result.trace_path = trace_path
        attempts.append(result)

    passed = sum(1 for attempt in attempts if attempt.passed)
    failure_modes: dict[str, int] = {}
    for attempt in attempts:
        for failure in attempt.failures:
            failure_modes[failure] = failure_modes.get(failure, 0) + 1

    failed = any(not attempt.passed for attempt in attempts)
    return {
        "timestamp": time.time(),
        "prompt": prompt,
        **_run_metadata(settings),
        "n": n,
        "passed": passed,
        "pass_rate": passed / n if n else 0.0,
        "failure_modes": failure_modes,
        "attempt_timeout_seconds": timeout,
        "require_viz": require_viz_tool,
        "failure_trace_path": trace_path if failed else None,
        "attempts": [attempt.to_dict() for attempt in attempts],
    }
