import pytest

from ecommerce_agent.routing.keyword import KeywordRouter
from ecommerce_agent.routing.registry import build_specialist_registry


def _router() -> KeywordRouter:
    return KeywordRouter(build_specialist_registry())


@pytest.mark.asyncio
async def test_keyword_hit_routes_to_order_manager() -> None:
    decision = await _router().route("Create a purchase order to restock product 1")

    assert decision.specialist == "order-manager"
    assert decision.source == "keyword"


@pytest.mark.asyncio
async def test_no_keyword_routes_to_default() -> None:
    decision = await _router().route("Forecast next month sales by category")

    assert decision.specialist == "sales-analyst"
    assert decision.source == "keyword"


@pytest.mark.asyncio
async def test_keyword_router_accepts_and_ignores_history() -> None:
    decision = await _router().route(
        "Forecast next month sales by category",
        history=[{"role": "user", "content": "earlier create a purchase order"}],
    )

    assert decision.specialist == "sales-analyst"
    assert decision.source == "keyword"
