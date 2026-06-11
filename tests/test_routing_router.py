import pytest

from ecommerce_agent.routing.registry import build_specialist_registry
from ecommerce_agent.routing.router import (
    ClassifierOutput,
    ClassifierRouter,
    RouteDecision,
)


class FakeStructured:
    def __init__(
        self,
        result: ClassifierOutput | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result
        self._exc = exc
        self.calls: list = []

    async def ainvoke(self, messages: list) -> ClassifierOutput | None:
        self.calls.append(messages)
        if self._exc is not None:
            raise self._exc
        return self._result


class FakeModel:
    def __init__(self, structured: FakeStructured) -> None:
        self._structured = structured
        self.method: str | None = None

    def with_structured_output(
        self, schema: object, *, method: str | None = None
    ) -> FakeStructured:
        self.method = method
        return self._structured


def _router(
    result: ClassifierOutput | None = None,
    exc: Exception | None = None,
) -> tuple[ClassifierRouter, FakeModel]:
    model = FakeModel(FakeStructured(result=result, exc=exc))
    return ClassifierRouter(model, build_specialist_registry()), model


@pytest.mark.asyncio
async def test_valid_specialist_is_returned_from_classifier() -> None:
    router, model = _router(ClassifierOutput(specialist="order-manager", reason="po"))

    decision = await router.route("create a purchase order for 200 units")

    assert decision == RouteDecision(specialist="order-manager", source="classifier", reason="po")
    assert model.method == "function_calling"
    sent = model._structured.calls[0]
    assert sent[1].content == "create a purchase order for 200 units"
    assert "{specialists}" not in sent[0].content
    assert "{message}" not in sent[0].content


@pytest.mark.asyncio
async def test_unsure_falls_back_to_default() -> None:
    router, _ = _router(ClassifierOutput(specialist="unsure", reason="ambiguous"))

    decision = await router.route("hello")

    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_unregistered_name_falls_back_to_default() -> None:
    router, _ = _router(ClassifierOutput(specialist="wizard", reason="?"))

    decision = await router.route("hello")

    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_exception_falls_back_and_never_raises() -> None:
    router, _ = _router(exc=RuntimeError("boom"))

    decision = await router.route("hello")

    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"


@pytest.mark.asyncio
async def test_timeout_falls_back() -> None:
    router, _ = _router(exc=TimeoutError())

    decision = await router.route("hello")

    assert decision.specialist == "sales-analyst"
    assert decision.source == "fallback"
