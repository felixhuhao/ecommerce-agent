from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ecommerce_agent.routing.registry import SpecialistRegistry, build_specialist_registry
from ecommerce_agent.routing.router import RouteDecision, Router

_DATASET_PATH = Path(__file__).parent / "datasets" / "routing.yaml"
ERROR_PREDICTION = "<error>"
_VALID_HISTORY_ROLES = {"user", "assistant"}


@dataclass(frozen=True)
class RoutingCase:
    id: str
    prompt: str
    expected: str
    tags: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)


def _validate_history(case_id: str, raw_history: object) -> list[dict]:
    if raw_history is None:
        return []
    if not isinstance(raw_history, list):
        raise ValueError(f"case {case_id!r} history must be a list")

    history: list[dict] = []
    for entry in raw_history:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if role not in _VALID_HISTORY_ROLES:
            raise ValueError(f"case {case_id!r} history role must be user/assistant, got {role!r}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"case {case_id!r} history content must be a non-empty string")
        history.append({"role": role, "content": content})
    return history


def load_routing_cases(
    path: str | None = None,
    *,
    registry: SpecialistRegistry | None = None,
) -> list[RoutingCase]:
    target = Path(path) if path else _DATASET_PATH
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or []
    registry = registry or build_specialist_registry()
    cases: list[RoutingCase] = []
    for entry in raw:
        case = RoutingCase(
            id=entry["id"],
            prompt=entry["prompt"],
            expected=entry["expected"],
            tags=list(entry.get("tags", [])),
            history=_validate_history(entry["id"], entry.get("history")),
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
            decision = await router.route(case.prompt, history=case.history)
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
    candidate_ids = {r.case_id for r in candidate.cases}
    if set(baseline_passed) != candidate_ids:
        raise ValueError("baseline and candidate reports must cover the same case ids")

    improvements = [
        result.case_id
        for result in candidate.cases
        if not baseline_passed[result.case_id] and result.passed
    ]
    regressions = [
        result.case_id
        for result in candidate.cases
        if baseline_passed[result.case_id] and not result.passed
    ]
    return {
        "overall_delta": candidate.accuracy - baseline.accuracy,
        "adversarial_delta": candidate.per_tag_accuracy.get("adversarial", 0.0)
        - baseline.per_tag_accuracy.get("adversarial", 0.0),
        "improvements": improvements,
        "regressions": regressions,
        "flips": improvements + regressions,
    }


class LatestMessageRouter:
    """Adapter that runs an inner router on the latest message only."""

    def __init__(self, inner: Router) -> None:
        self._inner = inner

    async def route(self, message: str, *, history=()) -> RouteDecision:
        return await self._inner.route(message, history=())
