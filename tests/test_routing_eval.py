import pytest

from ecommerce_agent.evals.routing import (
    EvalReport,
    RoutingCase,
    compare,
    load_routing_cases,
    run_routing_eval,
    score_case,
)
from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import RouteDecision


def _case(cid: str, expected: str, tags: tuple[str, ...] = ()) -> RoutingCase:
    return RoutingCase(id=cid, prompt=cid, expected=expected, tags=list(tags))


def test_score_case_pass_and_fail() -> None:
    case = _case("a", "order-manager", ("adversarial",))

    ok = score_case(RouteDecision("order-manager", "classifier", "r"), case)
    bad = score_case(RouteDecision("sales-analyst", "classifier", "r"), case)

    assert ok.passed is True and ok.predicted == "order-manager"
    assert bad.passed is False


class StubRouter:
    def __init__(self, mapping: dict[str, str], errors: tuple[str, ...] = ()) -> None:
        self._mapping = mapping
        self._errors = set(errors)

    async def route(self, message: str) -> RouteDecision:
        if message in self._errors:
            raise RuntimeError("boom")
        return RouteDecision(self._mapping[message], "classifier", "r")


@pytest.mark.asyncio
async def test_run_routing_eval_aggregates_accuracy_and_confusion() -> None:
    cases = [
        _case("p1", "sales-analyst", ("straightforward",)),
        _case("p2", "order-manager", ("adversarial",)),
        _case("p3", "order-manager", ("adversarial",)),
    ]
    router = StubRouter({"p1": "sales-analyst", "p2": "order-manager", "p3": "sales-analyst"})

    report = await run_routing_eval(router, cases, router_name="stub")

    assert isinstance(report, EvalReport)
    assert report.n == 3
    assert report.passed == 2
    assert report.errors == 0
    assert report.accuracy == pytest.approx(2 / 3)
    assert report.per_tag_accuracy["adversarial"] == pytest.approx(0.5)
    assert report.confusion["order-manager"]["sales-analyst"] == 1
    assert report.confusion["order-manager"]["order-manager"] == 1


@pytest.mark.asyncio
async def test_errored_case_excluded_from_confusion_but_counts_as_failure() -> None:
    cases = [_case("p1", "sales-analyst"), _case("boom", "order-manager")]
    router = StubRouter({"p1": "sales-analyst"}, errors=("boom",))

    report = await run_routing_eval(router, cases, router_name="stub")

    assert report.errors == 1
    assert report.passed == 1
    assert report.accuracy == pytest.approx(0.5)
    assert "<error>" not in report.confusion.get("order-manager", {})


@pytest.mark.asyncio
async def test_compare_reports_overall_and_adversarial_delta() -> None:
    cases = [_case("p1", "order-manager", ("adversarial",))]
    keyword = await run_routing_eval(
        StubRouter({"p1": "sales-analyst"}), cases, router_name="keyword"
    )
    classifier = await run_routing_eval(
        StubRouter({"p1": "order-manager"}), cases, router_name="classifier"
    )

    delta = compare(keyword, classifier)

    assert delta["overall_delta"] == pytest.approx(1.0)
    assert delta["adversarial_delta"] == pytest.approx(1.0)
    assert delta["flips"] == ["p1"]


@pytest.mark.asyncio
async def test_keyword_baseline_over_dataset_is_deterministic() -> None:
    cases = load_routing_cases()

    report = await run_routing_eval(
        KeywordRouter(build_specialist_registry()),
        cases,
        router_name="keyword",
    )

    assert report.errors == 0
    assert report.per_tag_accuracy["adversarial"] == 0.0
    assert report.accuracy < 0.80
