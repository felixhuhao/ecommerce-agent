import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import run_metadata
from ecommerce_agent.evals.routing import compare, load_routing_cases, run_routing_eval
from ecommerce_agent.models import classifier_model_params, get_classifier_model
from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter
from ecommerce_agent.trace.jsonl import append_eval_baseline


@pytest.mark.integration
@pytest.mark.live
async def test_classifier_beats_keyword_on_adversarial(tmp_path) -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live routing eval")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    cases = load_routing_cases()
    registry = build_specialist_registry()

    keyword_report = await run_routing_eval(
        KeywordRouter(registry),
        cases,
        router_name="keyword",
    )
    classifier_report = await run_routing_eval(
        ClassifierRouter(get_classifier_model(settings), registry),
        cases,
        router_name="classifier",
    )

    entry = {
        **run_metadata(
            settings,
            prompt_name="router_classifier",
            model=classifier_model_params(settings),
        ),
        "router_name": classifier_report.router_name,
        "n": classifier_report.n,
        "accuracy": classifier_report.accuracy,
        "per_tag_accuracy": classifier_report.per_tag_accuracy,
        "confusion": classifier_report.confusion,
    }
    append_eval_baseline(entry, str(tmp_path / "routing-baseline.jsonl"))

    delta = compare(keyword_report, classifier_report)
    assert (
        classifier_report.per_tag_accuracy["adversarial"]
        > keyword_report.per_tag_accuracy["adversarial"]
    )
    assert classifier_report.accuracy >= 0.80
    assert delta["overall_delta"] >= 0
