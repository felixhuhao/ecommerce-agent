from __future__ import annotations

from ecommerce_agent.routing.registry import SpecialistRegistry
from ecommerce_agent.routing.router import RouteDecision

# Ported from the former sessions/factory.py keyword shortcut. Eval baseline only.
ORDER_MANAGER_KEYWORDS = (
    "approval",
    "approve",
    "create purchase order",
    "purchase order",
    "receive purchase",
    "receive po",
    "replenish",
    "restock",
    "update order",
    "order status",
)


class KeywordRouter:
    """Substring keyword router: the eval's before-classifier baseline."""

    def __init__(self, registry: SpecialistRegistry) -> None:
        self._registry = registry

    async def route(self, message: str) -> RouteDecision:
        lowered = message.lower()
        if any(keyword in lowered for keyword in ORDER_MANAGER_KEYWORDS):
            return RouteDecision(
                specialist="order-manager",
                source="keyword",
                reason="keyword match",
            )
        return RouteDecision(
            specialist=self._registry.default.name,
            source="keyword",
            reason="no keyword",
        )
