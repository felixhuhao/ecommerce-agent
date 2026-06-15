from pathlib import Path

import pytest

from ecommerce_agent.evals.routing import (
    LatestMessageRouter,
    compare,
    load_routing_cases,
    run_routing_eval,
)
from ecommerce_agent.routing.router import RouteDecision

_MT_PATH = str(
    Path(__file__).parent.parent
    / "src"
    / "ecommerce_agent"
    / "evals"
    / "datasets"
    / "routing_multiturn.yaml"
)


def test_multiturn_dataset_loads_and_is_well_formed() -> None:
    cases = load_routing_cases(_MT_PATH)
    assert len(cases) >= 5
    assert all("multi-turn" in c.tags for c in cases)
    assert all(len(c.history) >= 1 for c in cases)
    assert all(c.expected in {"sales-analyst", "purchasing"} for c in cases)


class _ContextAwareStub:
    """Routes correctly only when history is present; latest-only routes wrong."""

    async def route(self, message: str, *, history=()) -> RouteDecision:
        if not history:
            return RouteDecision("sales-analyst", "fallback", "no context")

        joined = " ".join(h["content"].lower() for h in history)
        if "purchase order" in joined or "po " in joined or "replenish" in joined:
            return RouteDecision("purchasing", "classifier", "ctx: write thread")
        return RouteDecision("sales-analyst", "classifier", "ctx: analysis thread")


@pytest.mark.asyncio
async def test_context_aware_beats_latest_only_offline() -> None:
    cases = [c for c in load_routing_cases(_MT_PATH) if c.expected == "purchasing"]
    assert cases, "expected at least one purchasing multi-turn case"
    stub = _ContextAwareStub()

    baseline = await run_routing_eval(LatestMessageRouter(stub), cases, router_name="latest-only")
    candidate = await run_routing_eval(stub, cases, router_name="context-aware")
    delta = compare(baseline, candidate)

    assert candidate.accuracy > baseline.accuracy
    assert delta["overall_delta"] > 0
