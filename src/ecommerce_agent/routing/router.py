from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ecommerce_agent.models import (
    CLASSIFIER_STRUCTURED_OUTPUT_METHOD,
    CLASSIFIER_TIMEOUT_SECONDS,
)
from ecommerce_agent.prompts.loader import get_prompt
from ecommerce_agent.routing.registry import SpecialistRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteDecision:
    specialist: str
    source: str
    reason: str


class Router(Protocol):
    async def route(self, message: str) -> RouteDecision: ...


class ClassifierOutput(BaseModel):
    specialist: str = Field(description="a registered specialist name, or 'unsure'")
    reason: str = Field(description="brief reason")


class ClassifierRouter:
    """Model-based router: one structured call with a safe fallback."""

    def __init__(self, model: Any, registry: SpecialistRegistry) -> None:
        self._model = model
        self._registry = registry

    async def route(self, message: str) -> RouteDecision:
        instruction = get_prompt("router_classifier").replace(
            "{specialists}", self._registry.describe()
        )
        structured = self._model.with_structured_output(
            ClassifierOutput, method=CLASSIFIER_STRUCTURED_OUTPUT_METHOD
        )
        try:
            out = await asyncio.wait_for(
                structured.ainvoke(
                    [SystemMessage(content=instruction), HumanMessage(content=message)]
                ),
                timeout=CLASSIFIER_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - routing failures must fall back.
            logger.warning("classifier routing failed; using default", exc_info=True)
            return self._fallback("classifier call failed")

        if self._registry.is_registered(out.specialist):
            return RouteDecision(
                specialist=out.specialist,
                source="classifier",
                reason=out.reason,
            )
        return self._fallback(f"classifier returned {out.specialist!r}")

    def _fallback(self, reason: str) -> RouteDecision:
        return RouteDecision(
            specialist=self._registry.default.name,
            source="fallback",
            reason=reason,
        )
