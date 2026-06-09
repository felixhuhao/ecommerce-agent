"""On-demand N-run reliability harness for the M1 forecast hero."""

from __future__ import annotations

import hashlib
import importlib.metadata
import subprocess
import time
from dataclasses import dataclass, field

from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import WRITE_OR_APPROVAL_SPRING_TOOLS
from ecommerce_agent.trace.schema import TraceRecord

HERO_PROMPT = (
    "Which categories are trending up or down over the last 6 months, forecast next "
    "month's sales, and chart the result. If product_query does not return a product "
    "ID from an order item, bucket it as unknown and continue. Keep the summary short."
)


@dataclass
class AttemptResult:
    passed: bool
    failures: list[str] = field(default_factory=list)


def assess_attempt(record: TraceRecord, stream_body: str) -> AttemptResult:
    """Structural pass/fail for one hero attempt. No semantic judgement."""
    failures: list[str] = []
    tools = set(record.tool_names())

    if "order_query" not in tools:
        failures.append("order_query not called")
    leaked = tools & set(WRITE_OR_APPROVAL_SPRING_TOOLS)
    if leaked:
        failures.append(f"write/approval tools appeared: {sorted(leaked)}")
    if not ({"execute"} & tools or "generate_visualization" in tools):
        failures.append("neither execute nor generate_visualization was called")
    if "event: error" in stream_body or "event: done" not in stream_body:
        failures.append("stream did not complete cleanly")

    return AttemptResult(passed=not failures, failures=failures)


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


def run_reliability(n: int, settings: Settings, *, prompt: str = HERO_PROMPT) -> dict:
    """Run the hero prompt `n` times against a fresh app and return a batch report."""
    from fastapi.testclient import TestClient

    from ecommerce_agent.api.app import create_app

    attempts: list[AttemptResult] = []
    app = create_app(settings=settings)
    with TestClient(app) as client:
        for _ in range(n):
            with client.stream("POST", "/api/chat/stream", json={"message": prompt}) as response:
                body = "".join(response.iter_text())
            record = app.state.last_trace or TraceRecord()
            attempts.append(assess_attempt(record, body))

    passed = sum(1 for attempt in attempts if attempt.passed)
    failure_modes: dict[str, int] = {}
    for attempt in attempts:
        for failure in attempt.failures:
            failure_modes[failure] = failure_modes.get(failure, 0) + 1

    return {
        "timestamp": time.time(),
        "prompt": prompt,
        **_run_metadata(settings),
        "n": n,
        "passed": passed,
        "pass_rate": passed / n if n else 0.0,
        "failure_modes": failure_modes,
    }
