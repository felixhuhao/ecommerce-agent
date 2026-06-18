"""Tier 1 live demo smoke.

Exercises the real FastAPI session path (routing, MCP tools, trace, grounding, thread
persistence) against the configured live model. Skips unless ``RUN_LIVE_LLM=1`` is set.

Each case asserts route, required/forbidden tools, fanout budgets, grounding authority,
and artifact/proposal presence. On failure a compact diagnostic line is appended to
``.pytest_cache/demo_live_smoke_diagnostics.jsonl``.
"""

from __future__ import annotations

import json
import os
import signal
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ecommerce_agent.api.app import create_app
from ecommerce_agent.auth.dependencies import current_actor
from ecommerce_agent.auth.models import Actor, Role
from ecommerce_agent.config import Settings
from ecommerce_agent.mcp_client import NL2SQL_TOOLS, VIZ_TOOLS, WRITE_SPRING_TOOLS
from ecommerce_agent.tools.analytics import (
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME,
    SALES_BY_CATEGORY_TOOL_NAME,
)
from ecommerce_agent.tools.charting import CREATE_CHART_SPEC_TOOL_NAME
from tests.integration.helpers import (
    skip_unless_docker_available,
    skip_unless_mongo_is_running,
    skip_unless_nl2sql_mcp_is_running,
    skip_unless_spring_mcp_is_running,
)

pytestmark = [pytest.mark.integration, pytest.mark.live]

OPERATOR = Actor(
    user_id="op1", username="op1", role=Role.OPERATOR, spring_user_id=1
)

CASE_TIMEOUT_SECONDS = 150
POLL_INTERVAL_SECONDS = 0.25

MAX_TOTAL_TOOL_CALLS = 12
MAX_SAME_TOOL_CALLS = {
    "order_query": 2,
    "user_query": 2,
    "product_query": 2,
    "product_search": 2,
    "inventory_query": 2,
    "stage_sales_analysis_inputs": 2,
    "query_readonly": 3,
    "get_table_schema": 3,
    CUSTOMER_SPEND_SUMMARY_TOOL_NAME: 1,
    SALES_BY_CATEGORY_TOOL_NAME: 1,
    CREATE_CHART_SPEC_TOOL_NAME: 1,
}
ALWAYS_FORBIDDEN = frozenset({"task", "write_todos"})
SANDBOX_TOOLS = frozenset({"execute", "stage_sales_analysis_inputs"})
SANDBOX_CONTROL_TOOLS = SANDBOX_TOOLS | frozenset({"write_file"})
LEGACY_CHART_TOOLS = VIZ_TOOLS - {CREATE_CHART_SPEC_TOOL_NAME}

_DIAG_PATH = Path(".pytest_cache") / "demo_live_smoke_diagnostics.jsonl"


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    specialists: tuple[str, ...]
    required_all_of: tuple[str, ...] = ()
    required_any_of: tuple[frozenset[str], ...] = ()
    forbidden: frozenset[str] = field(default_factory=frozenset)
    needs_sandbox: bool = False
    expects_artifact: bool = False
    expects_no_artifact: bool = False
    expected_artifact_kind: str | None = None
    expected_chart_types: frozenset[str] = field(default_factory=frozenset)
    expects_proposal: bool = False
    authorities: tuple[str, ...] | None = None
    requires_nl2sql: bool = False


CASES = [
    Case(
        id="inventory_low_stock_sku",
        prompt="is SKU-LOW-003 below safety stock?",
        specialists=("inventory",),
        required_any_of=(frozenset({"inventory_low_stock", "inventory_query"}),),
        forbidden=WRITE_SPRING_TOOLS | VIZ_TOOLS | NL2SQL_TOOLS,
        authorities=("authoritative",),
    ),
    Case(
        id="customer_top_spend",
        prompt="who are our top customers by spend?",
        specialists=("customer-insights",),
        required_all_of=(CUSTOMER_SPEND_SUMMARY_TOOL_NAME,),
        forbidden=WRITE_SPRING_TOOLS | SANDBOX_CONTROL_TOOLS | NL2SQL_TOOLS,
        authorities=("authoritative",),
    ),
    Case(
        id="customer_groups_spend",
        prompt=(
            "Which customer segments or groups are spending the most? "
            "Include a chart if useful."
        ),
        specialists=("customer-insights",),
        required_all_of=(CUSTOMER_SPEND_SUMMARY_TOOL_NAME,),
        forbidden=WRITE_SPRING_TOOLS | SANDBOX_CONTROL_TOOLS | NL2SQL_TOOLS,
        authorities=("authoritative",),
    ),
    Case(
        id="sales_category_chart",
        prompt="compare sales by category and chart it",
        specialists=("sales-analyst",),
        required_all_of=(SALES_BY_CATEGORY_TOOL_NAME, CREATE_CHART_SPEC_TOOL_NAME),
        forbidden=WRITE_SPRING_TOOLS | LEGACY_CHART_TOOLS | SANDBOX_CONTROL_TOOLS | NL2SQL_TOOLS,
        expects_artifact=True,
        expected_artifact_kind="echarts",
        expected_chart_types=frozenset({"bar", "column", "pie"}),
        authorities=("authoritative",),
    ),
    Case(
        id="forecast_chart",
        prompt="forecast SKU-LOW-003 sales next month and chart it",
        specialists=("sales-analyst",),
        required_all_of=(
            "stage_sales_analysis_inputs",
            "execute",
            CREATE_CHART_SPEC_TOOL_NAME,
        ),
        expects_artifact=True,
        expected_artifact_kind="echarts",
        expected_chart_types=frozenset({"line", "area"}),
        needs_sandbox=True,
        authorities=("derived", "authoritative"),
    ),
    Case(
        id="purchase_order_proposal",
        prompt="create a purchase order for 200 units of productId 9 from supplier 7",
        specialists=("purchasing",),
        required_all_of=("request_approval",),
        forbidden=WRITE_SPRING_TOOLS,
        expects_proposal=True,
    ),
    Case(
        id="order_status_change",
        prompt="cancel pending order 1008",
        specialists=("order-manager",),
        required_all_of=("request_approval",),
        forbidden=WRITE_SPRING_TOOLS | VIZ_TOOLS | {"get_statistics"},
        expects_proposal=True,
    ),
    Case(
        id="invalid_sku_graceful",
        prompt="forecast SKU-NOPE-999 next month and chart it",
        specialists=("sales-analyst", "inventory"),
        forbidden=WRITE_SPRING_TOOLS | VIZ_TOOLS,
        expects_no_artifact=True,
    ),
    Case(
        id="warehouse_cohort",
        prompt="show repeat purchase rate by customer cohort over the last 12 months",
        specialists=("data-warehouse-analyst",),
        required_all_of=("query_readonly",),
        forbidden=WRITE_SPRING_TOOLS | VIZ_TOOLS | {"request_approval"},
        authorities=("authoritative",),
        requires_nl2sql=True,
    ),
    Case(
        id="warehouse_region_channel_chart",
        prompt="break down last 90 days revenue by region and channel as a chart",
        specialists=("data-warehouse-analyst",),
        required_all_of=("query_readonly", CREATE_CHART_SPEC_TOOL_NAME),
        forbidden=WRITE_SPRING_TOOLS | LEGACY_CHART_TOOLS | {"request_approval"},
        expects_artifact=True,
        expected_artifact_kind="echarts",
        expected_chart_types=frozenset({"bar", "column", "pie"}),
        authorities=("authoritative",),
        requires_nl2sql=True,
    ),
    Case(
        id="warehouse_current_stock_boundary",
        prompt="current stock from the data warehouse for SKU-LOW-003",
        specialists=("inventory",),
        required_any_of=(frozenset({"inventory_low_stock", "inventory_query"}),),
        forbidden=WRITE_SPRING_TOOLS | VIZ_TOOLS | NL2SQL_TOOLS,
        authorities=("authoritative",),
        requires_nl2sql=True,
    ),
]


def _skip_unless_enabled() -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live demo smoke")


def _skip_unless_sandbox_image(settings: Settings) -> None:
    skip_unless_docker_available()
    import docker

    try:
        docker.from_env().images.get(settings.sandbox_image)
    except Exception:
        pytest.skip(f"sandbox image {settings.sandbox_image} is not built")


@contextmanager
def _fail_after(seconds: int) -> Iterator[None]:
    def raise_timeout(signum, frame):  # noqa: ARG001
        raise TimeoutError(f"demo live smoke case exceeded {seconds}s")

    previous = signal.signal(signal.SIGALRM, raise_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _settings() -> Settings:
    return Settings(
        mcp_request_timeout_seconds=15,
        mcp_sse_read_timeout_seconds=120,
    )


def _wait_for_terminal_message(
    client: TestClient, session_id: str, timeout: float
) -> tuple[dict | None, dict]:
    deadline = time.monotonic() + timeout
    last_thread: dict = {"messages": []}
    while time.monotonic() < deadline:
        last_thread = client.get(f"/api/sessions/{session_id}/thread").json()
        agent_messages = [
            m
            for m in last_thread["messages"]
            if m["type"] in ("agent_answer", "agent_proposal")
        ]
        if agent_messages:
            return agent_messages[-1], last_thread
        time.sleep(POLL_INTERVAL_SECONDS)
    return None, last_thread


def _run_case(client: TestClient, case: Case) -> dict:
    session_id = client.post("/api/sessions").json()["session_id"]
    post = client.post(
        f"/api/sessions/{session_id}/messages", json={"message": case.prompt}
    )
    assert post.status_code == 202, f"POST messages -> {post.status_code}: {post.text}"
    turn_id = post.json()["turn_id"]

    terminal, _thread = _wait_for_terminal_message(
        client, session_id, CASE_TIMEOUT_SECONDS
    )

    timeline = client.get(
        f"/api/sessions/{session_id}/turns/{turn_id}/trace"
    ).json()
    spans = timeline.get("spans", [])
    route = next(
        (s["name"] for s in spans if s.get("kind") == "route_decision"), None
    )
    tools = [s["name"] for s in spans if s.get("kind") == "tool_call"]

    terminal_type = terminal.get("type") if terminal else None
    grounding = (terminal or {}).get("grounding") or {}
    authority = grounding.get("authority")
    result = (terminal or {}).get("result") or {}
    artifacts = result.get("artifacts") or []
    proposal = terminal if terminal_type == "agent_proposal" else None
    answer_text = (terminal or {}).get("content") or ""

    sandbox_ms = sum(
        s.get("duration_ms") or 0
        for s in spans
        if s.get("kind") == "tool_call" and s.get("name") in SANDBOX_TOOLS
    )
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "route": route,
        "tools": tools,
        "terminal_type": terminal_type,
        "authority": authority,
        "artifacts": artifacts,
        "proposal": proposal,
        "answer_text": answer_text,
        "sandbox_ms": sandbox_ms,
        "trace_url": f"/api/sessions/{session_id}/turns/{turn_id}/trace",
    }


def _assert_case(case: Case, ctx: dict) -> None:
    assert ctx["terminal_type"] in ("agent_answer", "agent_proposal"), (
        f"turn did not produce a terminal agent message; "
        f"last type={ctx['terminal_type']!r}"
    )

    assert ctx["route"] in case.specialists, (
        f"route {ctx['route']!r} not in expected {case.specialists}"
    )

    tool_set = set(ctx["tools"])
    forbidden = case.forbidden | ALWAYS_FORBIDDEN
    hit = tool_set & forbidden
    assert not hit, f"forbidden tools fired: {sorted(hit)}"

    missing_required = [t for t in case.required_all_of if t not in tool_set]
    assert not missing_required, f"required tools missing (all_of): {missing_required}"

    for group in case.required_any_of:
        assert any(t in tool_set for t in group), (
            f"no tool from required group fired (any_of): {sorted(group)}"
        )

    counts = Counter(ctx["tools"])
    over_budget = {
        name: count
        for name, count in counts.items()
        if name in MAX_SAME_TOOL_CALLS and count > MAX_SAME_TOOL_CALLS[name]
    }
    assert not over_budget, f"repeated-tool fanout exceeded budget: {over_budget}"
    # Per design §9, sandbox cases may repeat `execute` as long as the turn finishes
    # within the case timeout and produces its artifact (both asserted elsewhere).
    # Only `execute` is exempt from the generic total; staging is backend data
    # fetching and stays capped (MAX_SAME_TOOL_CALLS) so it can't hide a read loop.
    counted = ctx["tools"] if not case.needs_sandbox else [
        t for t in ctx["tools"] if t != "execute"
    ]
    assert len(counted) <= MAX_TOTAL_TOOL_CALLS, (
        f"total tool calls {len(counted)} > {MAX_TOTAL_TOOL_CALLS}"
    )

    if case.authorities is not None:
        authority = ctx["authority"] or "not_applicable"
        assert authority in case.authorities, (
            f"grounding authority {authority!r} not in expected {case.authorities}"
        )

    if case.expects_artifact:
        assert ctx["artifacts"], "expected a chart artifact but none was attached"
    if case.expected_artifact_kind:
        kinds = {artifact.get("kind") for artifact in ctx["artifacts"]}
        assert case.expected_artifact_kind in kinds, (
            f"expected artifact kind {case.expected_artifact_kind!r}; got {ctx['artifacts']}"
        )
    if case.expected_chart_types:
        chart_types = {
            artifact.get("chart_type")
            for artifact in ctx["artifacts"]
            if artifact.get("kind") == "echarts"
        }
        assert chart_types & set(case.expected_chart_types), (
            f"expected ECharts type in {sorted(case.expected_chart_types)}; "
            f"got {sorted(t for t in chart_types if t)}"
        )
    if case.expects_no_artifact:
        assert not ctx["artifacts"], (
            f"expected no artifact for no-data prompt; got: {ctx['artifacts']}"
        )
    if case.expects_proposal:
        proposal = ctx["proposal"]
        assert proposal is not None, "expected a pending proposal (agent_proposal)"
        assert proposal.get("approval_id"), (
            f"proposal missing approval_id: {proposal}"
        )


def _write_diagnostic(case: Case, ctx: dict, exc: BaseException) -> None:
    if not ctx:
        return
    tools = ctx.get("tools") or []
    counts = Counter(tools)
    sandbox_counts = {name: counts.get(name, 0) for name in sorted(SANDBOX_TOOLS)}
    warehouse_counts = {name: counts.get(name, 0) for name in sorted(NL2SQL_TOOLS)}
    line = {
        "case_id": case.id,
        "prompt": case.prompt,
        "session_id": ctx.get("session_id"),
        "turn_id": ctx.get("turn_id"),
        "route": ctx.get("route"),
        "terminal_type": ctx.get("terminal_type"),
        "answer_tail": (ctx.get("answer_text") or "")[-300:],
        "ordered_tools": tools,
        "repeated_tool_counts": dict(counts),
        "sandbox": {
            "counts": sandbox_counts,
            "wall_ms": ctx.get("sandbox_ms"),
        },
        "warehouse": {"counts": warehouse_counts},
        "authority": ctx.get("authority"),
        "artifacts": len(ctx.get("artifacts") or []),
        "proposal_status": (ctx.get("proposal") or {}).get("status"),
        "trace_url": ctx.get("trace_url"),
        "error": f"{type(exc).__name__}: {exc}",
    }
    _DIAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _DIAG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_demo_live_case(case: Case) -> None:
    _skip_unless_enabled()
    settings = _settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")
    await skip_unless_spring_mcp_is_running(settings)
    if case.requires_nl2sql:
        await skip_unless_nl2sql_mcp_is_running(settings)
    await skip_unless_mongo_is_running(settings)
    if case.needs_sandbox:
        _skip_unless_sandbox_image(settings)

    app = create_app(settings=settings)
    app.dependency_overrides[current_actor] = lambda: OPERATOR
    ctx: dict = {}
    with TestClient(app) as client:
        try:
            with _fail_after(CASE_TIMEOUT_SECONDS):
                ctx = _run_case(client, case)
                _assert_case(case, ctx)
        except AssertionError as exc:
            _write_diagnostic(case, ctx, exc)
            raise
        except TimeoutError as exc:
            _write_diagnostic(case, ctx, exc)
            pytest.fail(str(exc))
