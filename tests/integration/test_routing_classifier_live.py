import os

import pytest

from ecommerce_agent.config import Settings
from ecommerce_agent.models import get_classifier_model
from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import ClassifierRouter


@pytest.mark.integration
@pytest.mark.live
async def test_classifier_routes_clear_prompts_live() -> None:
    if os.getenv("RUN_LIVE_LLM") != "1":
        pytest.skip("Set RUN_LIVE_LLM=1 to run the live classifier spike")
    settings = Settings()
    if not settings.llm_api_key:
        pytest.skip("LLM_API_KEY required")

    router = ClassifierRouter(get_classifier_model(settings), build_specialist_registry())

    po = await router.route("create a purchase order for 200 units of SKU-9")
    assert po.source == "classifier"
    assert po.specialist == "purchasing"

    sales = await router.route("what were total sales by category last month?")
    assert sales.source == "classifier"
    assert sales.specialist == "sales-analyst"
