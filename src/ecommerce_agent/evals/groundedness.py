from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources

import yaml

Judge = Callable[[str, str], list["ClaimVerdict"]]


@dataclass(frozen=True)
class GroundednessCase:
    id: str
    prompt: str
    tags: list[str]


@dataclass(frozen=True)
class ClaimVerdict:
    verdict: str


@dataclass
class GroundednessCaseResult:
    case_id: str
    authority: str
    supported: int = 0
    partial: int = 0
    unsupported: int = 0
    diagnostic: str | None = None

    @property
    def claims(self) -> int:
        return self.supported + self.partial + self.unsupported


@dataclass
class GroundednessReport:
    n: int
    unsupported_claim_rate: float
    partial_rate: float
    total_claims: int
    per_authority: dict[str, dict[str, int]]
    cases: list[GroundednessCaseResult] = field(default_factory=list)


def load_groundedness_cases() -> list[GroundednessCase]:
    raw = (
        resources.files("ecommerce_agent.evals.datasets")
        .joinpath("groundedness.yaml")
        .read_text(encoding="utf-8")
    )
    data = yaml.safe_load(raw) or {}
    cases = []
    for entry in data.get("cases", []):
        if not entry.get("id") or not entry.get("prompt") or not entry.get("tags"):
            raise ValueError(f"invalid groundedness case: {entry!r}")
        cases.append(
            GroundednessCase(
                id=entry["id"],
                prompt=entry["prompt"],
                tags=list(entry["tags"]),
            )
        )
    return cases


def score_answer(
    *,
    case_id: str,
    answer: str,
    evidence: str,
    judge: Judge,
    authority: str,
) -> GroundednessCaseResult:
    result = GroundednessCaseResult(case_id=case_id, authority=authority)
    try:
        verdicts = judge(answer, evidence)
    except Exception as exc:
        result.unsupported = 1
        result.diagnostic = f"judge_error: {type(exc).__name__}"
        return result
    for verdict in verdicts:
        if verdict.verdict == "supported":
            result.supported += 1
        elif verdict.verdict == "partial":
            result.partial += 1
        else:
            result.unsupported += 1
    return result


def aggregate(results: list[GroundednessCaseResult]) -> GroundednessReport:
    total = sum(result.claims for result in results)
    unsupported = sum(result.unsupported for result in results)
    partial = sum(result.partial for result in results)
    per_authority: dict[str, dict[str, int]] = {}
    for result in results:
        bucket = per_authority.setdefault(
            result.authority,
            {"supported": 0, "partial": 0, "unsupported": 0},
        )
        bucket["supported"] += result.supported
        bucket["partial"] += result.partial
        bucket["unsupported"] += result.unsupported
    return GroundednessReport(
        n=len(results),
        unsupported_claim_rate=(unsupported / total) if total else 0.0,
        partial_rate=(partial / total) if total else 0.0,
        total_claims=total,
        per_authority=per_authority,
        cases=results,
    )
