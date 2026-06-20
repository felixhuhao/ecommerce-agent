from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.sandbox import BaseSandbox

Judge = Callable[[str, str], list["ClaimVerdict"]]
_VALID_VERDICTS = {"supported", "partial", "unsupported"}
_JUDGE_SYSTEM = (
    "You are a strict grounding judge. Given an analytical ANSWER and the EVIDENCE "
    "(tool outputs) behind it, extract each distinct numeric claim in the answer and decide "
    "whether the evidence supports it. Reply with ONLY JSON: "
    '{"claims": [{"verdict": "supported|partial|unsupported"}]}. '
    "If the answer makes no numeric claim, return an empty claims list."
)


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


def parse_judge_response(text: str) -> list[ClaimVerdict]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object in judge response")
    payload = json.loads(match.group(0))
    verdicts = []
    for claim in payload.get("claims", []):
        verdict = claim.get("verdict")
        if verdict not in _VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {verdict!r}")
        verdicts.append(ClaimVerdict(verdict=verdict))
    return verdicts


def _response_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    return str(content or "")


def make_llm_judge(model: Any) -> Judge:
    """Wrap a chat model into a Judge callable for live use."""

    def judge(answer: str, evidence: str) -> list[ClaimVerdict]:
        response = model.invoke(
            [
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": f"ANSWER:\n{answer}\n\nEVIDENCE:\n{evidence}"},
            ]
        )
        return parse_judge_response(_response_text(response))

    return judge


def evidence_for(record: Any) -> str:
    """Join data-bearing span evidence, falling back to result_summary for older traces."""
    from ecommerce_agent.trace.tools import is_data_bearing

    parts = []
    for event in record.events:
        if event.event_type == "tool_call" and event.phase == "end" and is_data_bearing(event.name):
            parts.append(f"[{event.name}] {event.evidence or event.result_summary or ''}")
    return "\n".join(parts)


class NoOpSandbox(BaseSandbox):
    @property
    def id(self) -> str:
        return "groundedness-noop-sandbox"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return ExecuteResponse(
            output=(
                "Forecast analysis: next month's sales are projected at 16,100. "
                "Electronics trend up 8%, Audio trend down 3%, and price/unit correlation is 0.42."
            ),
            exit_code=0,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error=None) for path, _content in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [FileDownloadResponse(path=path, content=b"", error=None) for path in paths]

    def close(self) -> None:
        return None

    def idle_seconds(self) -> float:
        return 0.0


async def run_groundedness_eval(settings: Any) -> GroundednessReport:
    from ecommerce_agent.evals.tool_choice import build_stub_sales_analyst
    from ecommerce_agent.grounding.build import build_grounding
    from ecommerce_agent.models import get_primary_model
    from ecommerce_agent.trace.capture import capture
    from ecommerce_agent.trace.schema import TraceRecord

    cases = load_groundedness_cases()
    analyst = build_stub_sales_analyst(settings, backend=NoOpSandbox())
    judge = make_llm_judge(get_primary_model(settings))
    results: list[GroundednessCaseResult] = []
    for case in cases:
        record = TraceRecord()
        raw = analyst.astream_events(
            {"messages": [{"role": "user", "content": case.prompt}]},
            config={"recursion_limit": settings.agent_recursion_limit},
            version="v2",
        )
        try:
            async for _ in capture(
                raw,
                record,
                evidence_max_chars=settings.grounding_evidence_max_chars,
            ):
                pass
        except Exception as exc:
            if type(exc).__name__ != "GraphRecursionError":
                raise
            continue
        record.finish()
        grounding = build_grounding(record)
        results.append(
            score_answer(
                case_id=case.id,
                answer=record.answer,
                evidence=evidence_for(record),
                judge=judge,
                authority=grounding.authority.value,
            )
        )
    return aggregate(results)
