import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import run_metadata
from ecommerce_agent.evals.tool_choice import (
    build_stub_sales_analyst,
    load_tool_choice_cases,
    run_tool_choice_eval,
)
from ecommerce_agent.trace.jsonl import append_eval_baseline


@pytest.mark.integration
@pytest.mark.live
async def test_sales_analyst_tool_choice_live(tmp_path) -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live tool-choice eval")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    cases = load_tool_choice_cases()
    agent = build_stub_sales_analyst(settings)

    report = await run_tool_choice_eval(agent, cases)

    entry = {
        **run_metadata(settings, prompt_name="sales_analyst"),
        "eval": "tool_choice",
        "accuracy": report.accuracy,
        "aggregate_authority_miss_rate": report.aggregate_authority_miss_rate,
        "per_tag_accuracy": report.per_tag_accuracy,
        "per_expected_tool_accuracy": report.per_expected_tool_accuracy,
        "post_choice_errors": report.post_choice_errors,
        "errors_before_choice": report.errors_before_choice,
    }
    append_eval_baseline(entry, str(tmp_path / "tool-choice-baseline.jsonl"))

    assert report.aggregate_authority_miss_rate <= 0.25
    assert report.accuracy >= 7 / 9
