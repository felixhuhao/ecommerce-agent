from ecommerce_agent.threads.messages import ThreadMessage


def test_thread_message_defaults_and_roundtrip() -> None:
    msg = ThreadMessage(session_id="s1", type="user", content="hello")

    assert msg.session_id == "s1"
    assert msg.type == "user"
    assert msg.seq == 0
    assert len(msg.message_id) == 32
    assert msg.created_at.endswith("+00:00")

    dumped = msg.model_dump()
    assert dumped["approval_id"] is None
    assert ThreadMessage(**dumped) == msg


def test_thread_message_proposal_fields() -> None:
    msg = ThreadMessage(
        session_id="s1",
        type="agent_proposal",
        content="Proposed PO #123",
        approval_id="a1",
        tool_name="purchase_order_create",
        status="pending",
        card={"summary": "Restock 500"},
    )

    assert msg.approval_id == "a1"
    assert msg.card == {"summary": "Restock 500"}
