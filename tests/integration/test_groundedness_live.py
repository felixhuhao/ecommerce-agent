import os

import pytest


@pytest.mark.integration
@pytest.mark.live
async def test_groundedness_live_gate(tmp_path) -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live groundedness eval")

    from ecommerce_agent.config import get_settings
    from ecommerce_agent.evals.groundedness import run_groundedness_eval
    from ecommerce_agent.evals.metadata import run_metadata
    from ecommerce_agent.trace.jsonl import append_eval_baseline

    settings = get_settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    report = await run_groundedness_eval(settings)
    append_eval_baseline(
        {
            **run_metadata(settings, prompt_name="sales_analyst"),
            "eval": "groundedness",
            "unsupported_claim_rate": report.unsupported_claim_rate,
            "partial_rate": report.partial_rate,
            "total_claims": report.total_claims,
            "per_authority": report.per_authority,
        },
        str(tmp_path / "groundedness-baseline.jsonl"),
    )

    assert report.unsupported_claim_rate == 0.0
    assert report.n >= 6
