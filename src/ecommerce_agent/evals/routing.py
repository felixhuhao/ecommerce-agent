from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import RouteDecision, Router

_DATASET_PATH = Path(__file__).parent / "datasets" / "routing.yaml"
ERROR_PREDICTION = "<error>"


@dataclass(frozen=True)
class RoutingCase:
    id: str
    prompt: str
    expected: str
    tags: list[str] = field(default_factory=list)


def load_routing_cases(path: str | None = None) -> list[RoutingCase]:
    target = Path(path) if path else _DATASET_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    registry = build_specialist_registry()
    cases: list[RoutingCase] = []
    for entry in raw:
        case = RoutingCase(
            id=entry["id"],
            prompt=entry["prompt"],
            expected=entry["expected"],
            tags=list(entry.get("tags", [])),
        )
        if not registry.is_registered(case.expected):
            raise ValueError(f"case {case.id!r} has unknown specialist {case.expected!r}")
        cases.append(case)
    return cases


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected: str
    predicted: str
    passed: bool
    source: str
    tags: list[str]


@dataclass(frozen=True)
class EvalReport:
    router_name: str
    n: int
    passed: int
    errors: int
    accuracy: float
    per_tag_accuracy: dict[str, float]
    confusion: dict[str, dict[str, int]]
    cases: list[CaseResult]


def score_case(decision: RouteDecision, case: RoutingCase) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        expected=case.expected,
        predicted=decision.specialist,
        passed=decision.specialist == case.expected,
        source=decision.source,
        tags=case.tags,
    )


async def run_routing_eval(
    router: Router,
    cases: list[RoutingCase],
    *,
    router_name: str,
) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        try:
            decision = await router.route(case.prompt)
            results.append(score_case(decision, case))
        except Exception:  # noqa: BLE001 - one bad case must not abort the batch.
            results.append(
                CaseResult(
                    case_id=case.id,
                    expected=case.expected,
                    predicted=ERROR_PREDICTION,
                    passed=False,
                    source="error",
                    tags=case.tags,
                )
            )

    passed = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.predicted == ERROR_PREDICTION)

    per_tag_accuracy: dict[str, float] = {}
    for tag in {tag for r in results for tag in r.tags}:
        tagged = [r for r in results if tag in r.tags]
        per_tag_accuracy[tag] = sum(r.passed for r in tagged) / len(tagged)

    confusion: dict[str, dict[str, int]] = {}
    for result in results:
        if result.predicted == ERROR_PREDICTION:
            continue
        confusion.setdefault(result.expected, {})
        confusion[result.expected][result.predicted] = (
            confusion[result.expected].get(result.predicted, 0) + 1
        )

    return EvalReport(
        router_name=router_name,
        n=len(cases),
        passed=passed,
        errors=errors,
        accuracy=passed / len(cases) if cases else 0.0,
        per_tag_accuracy=per_tag_accuracy,
        confusion=confusion,
        cases=results,
    )


def compare(baseline: EvalReport, candidate: EvalReport) -> dict[str, object]:
    baseline_passed = {r.case_id: r.passed for r in baseline.cases}
    flips = [
        result.case_id
        for result in candidate.cases
        if baseline_passed.get(result.case_id) != result.passed
    ]
    return {
        "overall_delta": candidate.accuracy - baseline.accuracy,
        "adversarial_delta": candidate.per_tag_accuracy.get("adversarial", 0.0)
        - baseline.per_tag_accuracy.get("adversarial", 0.0),
        "flips": flips,
    }
