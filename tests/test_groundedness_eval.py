from ecommerce_agent.evals.groundedness import (
    ClaimVerdict,
    GroundednessCaseResult,
    aggregate,
    evidence_for,
    load_groundedness_cases,
    parse_judge_response,
    score_answer,
)
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


def fake_judge_factory(verdicts):
    def judge(answer: str, evidence: str) -> list[ClaimVerdict]:
        return list(verdicts)

    return judge


def test_score_answer_counts_verdicts() -> None:
    judge = fake_judge_factory([ClaimVerdict("supported"), ClaimVerdict("unsupported")])

    result = score_answer(
        case_id="c1",
        answer="x",
        evidence="e",
        judge=judge,
        authority="authoritative",
    )

    assert result.supported == 1
    assert result.unsupported == 1
    assert result.claims == 2


def test_score_answer_bad_judgment_counts_unsupported() -> None:
    def judge(answer: str, evidence: str) -> list[ClaimVerdict]:
        raise ValueError("bad json")

    result = score_answer(
        case_id="c1",
        answer="x",
        evidence="e",
        judge=judge,
        authority="derived",
    )

    assert result.unsupported == 1
    assert result.diagnostic is not None


def test_aggregate_unsupported_claim_rate() -> None:
    results = [
        GroundednessCaseResult(
            case_id="a",
            authority="authoritative",
            supported=2,
            partial=0,
            unsupported=0,
        ),
        GroundednessCaseResult(
            case_id="b",
            authority="unverified",
            supported=0,
            partial=1,
            unsupported=1,
        ),
    ]

    report = aggregate(results)

    assert report.n == 2
    assert report.unsupported_claim_rate == 1 / 4
    assert report.partial_rate == 1 / 4
    assert report.per_authority["authoritative"]["unsupported"] == 0


def test_dataset_loads_with_family_tags() -> None:
    cases = load_groundedness_cases()

    assert len(cases) >= 6
    assert all(case.prompt and case.tags for case in cases)


def test_parse_judge_response_extracts_verdicts() -> None:
    raw = '{"claims": [{"verdict": "supported"}, {"verdict": "unsupported"}]}'

    verdicts = parse_judge_response(raw)

    assert [verdict.verdict for verdict in verdicts] == ["supported", "unsupported"]


def test_parse_judge_response_rejects_unknown_verdict() -> None:
    import pytest

    with pytest.raises(ValueError, match="invalid verdict"):
        parse_judge_response('{"claims": [{"verdict": "maybe"}]}')


def test_evidence_for_joins_data_bearing_spans_only() -> None:
    record = TraceRecord(
        events=[
            TraceEvent(
                event_type="tool_call",
                name="get_statistics",
                phase="end",
                evidence="stats evidence",
            ),
            TraceEvent(
                event_type="tool_call",
                name="write_file",
                phase="end",
                evidence="file evidence",
            ),
            TraceEvent(
                event_type="tool_call",
                name="execute",
                phase="end",
                result_summary="forecast evidence",
            ),
        ]
    )

    evidence = evidence_for(record)

    assert "[get_statistics] stats evidence" in evidence
    assert "[execute] forecast evidence" in evidence
    assert "write_file" not in evidence
