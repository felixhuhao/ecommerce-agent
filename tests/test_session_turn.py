from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from ecommerce_agent.routing.router import RouteDecision
from ecommerce_agent.sessions.bus import SessionBus
from ecommerce_agent.sessions.factory import POLICY_DENIED_MESSAGE, RoutedSessionAgent
from ecommerce_agent.sessions.turn import _grounding_payload, run_turn
from ecommerce_agent.threads.messages import ThreadMessage
from ecommerce_agent.threads.store import InMemoryThreadStore
from ecommerce_agent.trace.schema import TraceEvent, TraceRecord


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


class ApprovalWithDataAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_start",
            "name": "inventory_query",
            "run_id": "inventory",
            "data": {"input": {"sku": "SKU-9"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "inventory_query",
            "run_id": "inventory",
            "data": {"output": [{"sku": "SKU-9", "onHand": 2}]},
        }
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
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Proposed restock of 25 units.")},
        }


class NumericApprovalWithoutDataAgent:
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
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Proposed restock of 25 units.")},
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


class BigEvidenceAgent:
    async def astream_events(
        self,
        inputs: dict,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        yield {
            "event": "on_tool_start",
            "name": "get_statistics",
            "run_id": "stats",
            "data": {"input": {"metric": "sales"}},
        }
        yield {
            "event": "on_tool_end",
            "name": "get_statistics",
            "run_id": "stats",
            "data": {"output": "abcdef"},
        }
        yield {
            "event": "on_chat_model_stream",
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="Total was $12.")},
        }


class RecordingAgent:
    def __init__(self) -> None:
        self.seen_inputs: dict | None = None

    async def astream_events(self, inputs, config, version):
        self.seen_inputs = inputs
        yield {
            "event": "on_chat_model_stream",
            "run_id": "final",
            "data": {"chunk": SimpleNamespace(content="answer")},
        }


def test_grounding_payload_for_authoritative_answer() -> None:
    record = TraceRecord(
        answer="Total was $42,180.",
        events=[
            TraceEvent(
                event_type="tool_call",
                name="get_statistics",
                phase="start",
                tool_call_id="g1",
            ),
            TraceEvent(
                event_type="tool_call",
                name="get_statistics",
                phase="end",
                tool_call_id="g1",
                result_summary="rows",
                evidence="rows",
                args_summary="{}",
            ),
        ],
    )

    payload = _grounding_payload(record)

    assert payload is not None
    assert payload["authority"] == "authoritative"
    assert payload["sources"][0]["span_id"] == "g1"
    assert "evidence" not in payload["sources"][0]


def test_grounding_payload_none_for_not_applicable() -> None:
    assert _grounding_payload(TraceRecord(answer="Hi there.")) is None


def test_grounding_payload_can_suppress_source_less_proposal_noise() -> None:
    record = TraceRecord(answer="Proposed restock of 25 units.")

    assert _grounding_payload(record) is not None
    assert (
        _grounding_payload(record, suppress_unverified_without_sources=True) is None
    )


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
    assert messages[0].grounding is None


@pytest.mark.asyncio
async def test_run_turn_attaches_grounding_to_agent_answer() -> None:
    store = InMemoryThreadStore()

    await run_turn(
        agent=BigEvidenceAgent(),
        message="hello",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=80,
    )

    messages = await store.list_messages("s1")
    assert messages[0].grounding is not None
    assert messages[0].grounding["authority"] == "authoritative"
    assert messages[0].grounding["sources"][0]["tool_name"] == "get_statistics"
    assert "evidence" not in messages[0].grounding["sources"][0]


@pytest.mark.asyncio
async def test_run_turn_passes_evidence_cap_to_capture() -> None:
    record = await run_turn(
        agent=BigEvidenceAgent(),
        message="hello",
        session_id="s1",
        turn_id="t1",
        store=InMemoryThreadStore(),
        bus=SessionBus(),
        recursion_limit=80,
        evidence_max_chars=3,
    )

    stats = next(
        event for event in record.events if event.name == "get_statistics" and event.phase == "end"
    )
    assert stats.evidence == "abc"


@pytest.mark.asyncio
async def test_run_turn_prepends_prior_thread_history() -> None:
    store = InMemoryThreadStore()
    await store.append(ThreadMessage(session_id="s1", type="user", content="prior q", turn_id="t0"))
    await store.append(
        ThreadMessage(session_id="s1", type="agent_answer", content="prior a", turn_id="t0")
    )
    agent = RecordingAgent()

    await run_turn(
        agent=agent,
        message="follow up",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=5,
    )

    assert agent.seen_inputs is not None
    contents = [m["content"] for m in agent.seen_inputs["messages"]]
    assert contents == ["prior q", "prior a", "follow up"]


@pytest.mark.asyncio
async def test_run_turn_excludes_in_flight_user_message_by_turn_id() -> None:
    store = InMemoryThreadStore()
    await store.append(
        ThreadMessage(session_id="s1", type="user", content="same text", turn_id="t1")
    )
    agent = RecordingAgent()

    await run_turn(
        agent=agent,
        message="same text",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=5,
    )

    assert agent.seen_inputs is not None
    contents = [m["content"] for m in agent.seen_inputs["messages"]]
    assert contents == ["same text"]


@pytest.mark.asyncio
async def test_run_turn_degrades_to_single_message_when_history_load_fails() -> None:
    class FailingListStore(InMemoryThreadStore):
        async def list_messages(self, session_id: str):
            raise RuntimeError("mongo down")

    store = FailingListStore()
    agent = RecordingAgent()

    record = await run_turn(
        agent=agent,
        message="hello",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=5,
    )

    assert agent.seen_inputs is not None
    assert [m["content"] for m in agent.seen_inputs["messages"]] == ["hello"]
    assert record.answer == "answer"


@pytest.mark.asyncio
async def test_run_turn_records_route_decision_event() -> None:
    class StubRouter:
        async def route(self, message: str, *, history=()) -> RouteDecision:
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
async def test_run_turn_records_policy_denial_event_and_answer() -> None:
    class StubRouter:
        async def route(self, message: str, *, history=()) -> RouteDecision:
            return RouteDecision("order-manager", "classifier", "write intent")

    agent = RoutedSessionAgent(
        router=StubRouter(),
        agents={"sales-analyst": RecordingAgent()},
        default_specialist="sales-analyst",
    )
    store = InMemoryThreadStore()

    record = await run_turn(
        agent=agent,
        message="create a purchase order",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=5,
    )

    messages = await store.list_messages("s1")
    assert messages[0].content == POLICY_DENIED_MESSAGE
    assert any(
        event.event_type == "policy_denial"
        and event.name == "order-manager"
        and event.status == "denied"
        for event in record.events
    )


@pytest.mark.asyncio
async def test_cross_agent_memory_order_manager_sees_prior_analyst_answer() -> None:
    store = InMemoryThreadStore()
    await store.append(
        ThreadMessage(session_id="s1", type="user", content="how are electronics?", turn_id="t0")
    )
    await store.append(
        ThreadMessage(
            session_id="s1",
            type="agent_answer",
            content="Electronics are the worst performer.",
            turn_id="t0",
        )
    )

    order_manager = RecordingAgent()

    class StickyRouter:
        async def route(self, message: str, *, history=()) -> RouteDecision:
            return RouteDecision("order-manager", "classifier", "write intent")

    agent = RoutedSessionAgent(
        router=StickyRouter(),
        agents={"sales-analyst": RecordingAgent(), "order-manager": order_manager},
        default_specialist="sales-analyst",
    )

    await run_turn(
        agent=agent,
        message="restock the worst performer",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=5,
    )

    assert order_manager.seen_inputs is not None
    contents = [m["content"] for m in order_manager.seen_inputs["messages"]]
    assert "Electronics are the worst performer." in contents
    assert contents[-1] == "restock the worst performer"


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
async def test_run_turn_attaches_grounding_to_agent_proposal() -> None:
    store = InMemoryThreadStore()

    await run_turn(
        agent=ApprovalWithDataAgent(),
        message="restock product 1",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=80,
        approval_client=FakeApprovalClient(),
    )

    messages = await store.list_messages("s1")
    assert messages[0].type == "agent_proposal"
    assert messages[0].grounding is not None
    assert messages[0].grounding["authority"] == "unverified"
    assert [source["tool_name"] for source in messages[0].grounding["sources"]] == [
        "inventory_query"
    ]


@pytest.mark.asyncio
async def test_run_turn_suppresses_source_less_proposal_grounding() -> None:
    store = InMemoryThreadStore()

    await run_turn(
        agent=NumericApprovalWithoutDataAgent(),
        message="restock product 1",
        session_id="s1",
        turn_id="t1",
        store=store,
        bus=SessionBus(),
        recursion_limit=80,
        approval_client=FakeApprovalClient(),
    )

    messages = await store.list_messages("s1")
    assert messages[0].type == "agent_proposal"
    assert messages[0].grounding is None


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
