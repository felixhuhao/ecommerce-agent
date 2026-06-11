from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ecommerce_agent.routing.router import RouteDecision
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.factory import RoutedSessionAgent
from ecommerce_agent.sessions.turn import run_turn
from ecommerce_agent.threads.store import InMemoryThreadStore


class FakeAgent:
    async def astream_events(self, inputs: dict, config: dict, version: str) -> AsyncIterator[dict]:
        assert inputs["messages"][0]["content"] == "hello"
        assert version == "v2"
        yield {"event": "on_tool_start", "name": "inventory_query", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Inventory looks healthy.")},
        }
        yield {"event": "on_tool_end", "name": "inventory_query", "data": {}}


class FakeApprovalClient:
    async def get_approval(self, approval_id: str) -> dict:
        assert approval_id == "approval-1"
        return {
            "approvalId": approval_id,
            "toolName": "purchase_order_create",
            "operationType": "create",
            "status": "pending",
            "operationDetail": (
                '{"title":"Create purchase order","supplierId":7,'
                '"items":[{"productId":1,"quantity":25}]}'
            ),
        }


class ApprovalAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_start",
            "name": "request_approval",
            "data": {
                "input": {
                    "toolName": "purchase_order_create",
                    "operationType": "create",
                }
            },
        }
        yield {
            "event": "on_tool_end",
            "name": "request_approval",
            "data": {"output": {"approvalId": "approval-1"}},
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": SimpleNamespace(content="Proposed restock.")},
        }


class MultiStepAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        yield {
            "event": "on_chat_model_stream",
            "run_id": "planning",
            "data": {"chunk": SimpleNamespace(content="I will try different approaches.")},
        }
        yield {"event": "on_tool_start", "name": "order_query", "run_id": "tool", "data": {}}
        yield {"event": "on_tool_end", "name": "order_query", "run_id": "tool", "data": {}}
        yield {
            "event": "on_chat_model_stream",
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Final operator answer.")},
        }


class ChartAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_end",
            "name": "generate_line_chart",
            "run_id": "chart-run",
            "data": {
                "output": [
                    {
                        "type": "text",
                        "text": "data:image/svg+xml;base64,PHN2Zy8+",
                        "id": "chart-1",
                    }
                ]
            },
        }
        yield {
            "event": "on_chat_model_stream",
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Chart generated.")},
        }


@pytest.mark.asyncio
async def test_run_turn_publishes_events_and_appends_answer() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()
    seen: list[dict] = []

    async with bus.subscription("s1") as sub:
        await run_turn(
            agent=FakeAgent(),
            message="hello",
            session_id="s1",
            turn_id="t1",
            store=store,
            bus=bus,
            recursion_limit=80,
        )
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())

    kinds = [event["event"] for event in seen]
    assert "tool" in kinds
    assert "token" not in kinds
    assert "thread.append" in kinds
    assert kinds[-1] == "done"

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].content == "Inventory looks healthy."
    assert messages[0].turn_id == "t1"
    assert messages[0].actor_id == "agent"


@pytest.mark.asyncio
async def test_run_turn_records_route_decision_event() -> None:
    class StubRouter:
        async def route(self, message: str) -> RouteDecision:
            assert message == "what were sales last month?"
            return RouteDecision("sales-analyst", "classifier", "analytics")

    class LeafAgent:
        async def astream_events(
            self,
            inputs: dict,
            config: dict,
            version: str,
        ) -> AsyncIterator[dict]:
            yield {
                "event": "on_chat_model_stream",
                "run_id": "final",
                "data": {"chunk": SimpleNamespace(content="hi")},
            }

    agent = RoutedSessionAgent(
        router=StubRouter(),
        agents={"sales-analyst": LeafAgent(), "order-manager": LeafAgent()},
        default_specialist="sales-analyst",
    )

    record = await run_turn(
        agent=agent,
        message="what were sales last month?",
        session_id="s1",
        turn_id="t1",
        store=InMemoryThreadStore(),
        bus=SessionBus(),
        recursion_limit=5,
    )

    kinds = [(event.event_type, event.name) for event in record.events]
    assert ("route_decision", "sales-analyst") in kinds


@pytest.mark.asyncio
async def test_run_turn_does_not_expose_intermediate_model_narration() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()
    seen: list[dict] = []

    async with bus.subscription("s1") as sub:
        await run_turn(
            agent=MultiStepAgent(),
            message="hello",
            session_id="s1",
            turn_id="t1",
            store=store,
            bus=bus,
            recursion_limit=80,
        )
        while not sub.queue.empty():
            seen.append(sub.queue.get_nowait())

    assert [event["event"] for event in seen].count("token") == 0
    messages = await store.list_messages("s1")
    assert messages[0].content == "Final operator answer."
    assert "different approaches" not in messages[0].content


@pytest.mark.asyncio
async def test_run_turn_attaches_chart_artifacts_to_agent_answer() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ChartAgent(),
        message="hello",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
    )

    messages = await store.list_messages("s1")
    assert messages[0].content == "Chart generated."
    assert messages[0].result == {
        "artifacts": [
            {
                "id": "chart-1",
                "kind": "image",
                "mime_type": "image/svg+xml",
                "src": "data:image/svg+xml;base64,PHN2Zy8+",
                "tool_name": "generate_line_chart",
            }
        ]
    }


@pytest.mark.asyncio
async def test_run_turn_appends_agent_proposal_for_request_approval() -> None:
    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ApprovalAgent(),
        message="restock product 1",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
        approval_client=FakeApprovalClient(),
    )

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_proposal"]
    assert messages[0].approval_id == "approval-1"
    assert messages[0].tool_name == "purchase_order_create"
    assert messages[0].status == "pending"
    assert messages[0].content == "Proposed restock."
    assert messages[0].card is not None
    assert messages[0].card["title"] == "Create purchase order"


@pytest.mark.asyncio
async def test_run_turn_preserves_approval_id_when_card_fetch_fails() -> None:
    class FailingApprovalClient:
        async def get_approval(self, approval_id: str) -> dict:
            assert approval_id == "approval-1"
            raise RuntimeError("approval API down")

    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ApprovalAgent(),
        message="restock product 1",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
        approval_client=FailingApprovalClient(),
    )

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].status == "failed"
    assert messages[0].approval_id == "approval-1"
    assert "Approval approval-1 was created" in messages[0].content


@pytest.mark.asyncio
async def test_run_turn_handles_malformed_request_approval_result() -> None:
    class MalformedApprovalAgent:
        async def astream_events(
            self,
            inputs: dict,
            config: dict,
            version: str,
        ) -> AsyncIterator[dict]:
            yield {
                "event": "on_tool_end",
                "name": "request_approval",
                "data": {"output": {"status": "pending"}},
            }

    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=MalformedApprovalAgent(),
        message="restock product 1",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
        approval_client=FakeApprovalClient(),
    )

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].status == "failed"
    assert "approval id" in messages[0].content


@pytest.mark.asyncio
async def test_run_turn_failure_appends_durable_agent_answer() -> None:
    class ExplodingAgent:
        async def astream_events(
            self,
            inputs: dict,
            config: dict,
            version: str,
        ) -> AsyncIterator[dict]:
            raise RuntimeError("boom")
            yield

    store = InMemoryThreadStore()
    bus = SessionBus()

    await run_turn(
        agent=ExplodingAgent(),
        message="hi",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=bus,
        recursion_limit=80,
    )

    messages = await store.list_messages("s1")
    assert [message.type for message in messages] == ["agent_answer"]
    assert messages[0].status == "failed"
