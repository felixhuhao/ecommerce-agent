import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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


@pytest.mark.asyncio
async def test_history_is_rendered_as_preceding_role_messages() -> None:
    router, model = _router(ClassifierOutput(specialist="order-manager", reason="ctx"))

    await router.route(
        "yes, do that for 500 units",
        history=[
            {"role": "user", "content": "should we restock SKU-12?"},
            {"role": "assistant", "content": "It is low; I can propose a PO."},
        ],
    )

    sent = model._structured.calls[0]
    assert isinstance(sent[0], SystemMessage)
    assert isinstance(sent[1], HumanMessage)
    assert sent[1].content == "should we restock SKU-12?"
    assert isinstance(sent[2], AIMessage)
    assert sent[2].content == "It is low; I can propose a PO."
    assert isinstance(sent[3], HumanMessage)
    assert sent[3].content == "yes, do that for 500 units"
    assert len(sent) == 4


@pytest.mark.asyncio
async def test_empty_history_reproduces_slice1_two_message_shape() -> None:
    router, model = _router(ClassifierOutput(specialist="sales-analyst", reason="ok"))

    await router.route("what were sales last month?")

    sent = model._structured.calls[0]
    assert [type(m) for m in sent] == [SystemMessage, HumanMessage]
    assert sent[1].content == "what were sales last month?"


@pytest.mark.asyncio
async def test_router_decision_changes_with_history_present() -> None:
    class HistoryAwareStructured:
        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            has_prior = any(isinstance(m, HumanMessage | AIMessage) for m in messages[1:-1])
            specialist = "order-manager" if has_prior else "sales-analyst"
            return ClassifierOutput(specialist=specialist, reason="ctx")

    class HistoryAwareModel:
        def __init__(self, structured: HistoryAwareStructured) -> None:
            self._structured = structured

        def with_structured_output(self, schema: object, *, method: str | None = None):
            return self._structured

    registry = build_specialist_registry()
    model = HistoryAwareModel(HistoryAwareStructured())
    router = ClassifierRouter(model, registry)

    without = await router.route("do it")
    with_ctx = await router.route(
        "do it",
        history=[{"role": "user", "content": "propose a PO for SKU-12"}],
    )

    assert without.specialist == "sales-analyst"
    assert with_ctx.specialist == "order-manager"
