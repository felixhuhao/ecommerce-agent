import os
from pathlib import Path

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.evals.metadata import run_metadata
from ecommerce_agent.evals.routing import (
    LatestMessageRouter,
    compare,
    load_routing_cases,
    run_routing_eval,
)
from ecommerce_agent.models import classifier_model_params, get_classifier_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter
from ecommerce_agent.trace.jsonl import append_eval_baseline

_MT_PATH = str(
    Path(__file__).parent.parent.parent
    / "src"
    / "ecommerce_agent"
    / "evals"
    / "datasets"
    / "routing_multiturn.yaml"
)


@pytest.mark.integration
@pytest.mark.live
async def test_context_aware_beats_latest_only_live(tmp_path) -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live multi-turn routing eval")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    cases = load_routing_cases(_MT_PATH)
    registry = build_specialist_registry()
    classifier = ClassifierRouter(get_classifier_model(settings), registry)

    baseline = await run_routing_eval(
        LatestMessageRouter(classifier), cases, router_name="latest-only"
    )
    candidate = await run_routing_eval(classifier, cases, router_name="context-aware")
    delta = compare(baseline, candidate)

    entry = {
        **run_metadata(
            settings,
            prompt_name="router_classifier",
            model=classifier_model_params(settings),
        ),
        "eval": "routing_multiturn",
        "latest_only_accuracy": baseline.accuracy,
        "context_aware_accuracy": candidate.accuracy,
        "overall_delta": delta["overall_delta"],
    }
    append_eval_baseline(entry, str(tmp_path / "routing-multiturn-baseline.jsonl"))

    assert candidate.accuracy > baseline.accuracy
