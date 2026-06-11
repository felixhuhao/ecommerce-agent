from ecommerce_agent.threads.history import (
    AGENT_HISTORY_MAX_EXCHANGES,
    ROUTER_HISTORY_MAX_EXCHANGES,
    build_history,
    take_last_exchanges,
)
from ecommerce_agent.threads.messages import ThreadMessage


def _msg(type_, content, *, turn_id=None, status=None, approval_id=None):
    return ThreadMessage(
        session_id="s1",
        type=type_,
        content=content,
        turn_id=turn_id,
        status=status,
        approval_id=approval_id,
    )


def test_maps_user_and_agent_messages_to_roles() -> None:
    history = build_history([_msg("user", "hello"), _msg("agent_answer", "hi there")])

    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_proposal_and_breadcrumbs_render_as_assistant_content_only() -> None:
    history = build_history(
        [
            _msg("agent_proposal", "Proposed PO #4471."),
            _msg(
                "approval_status",
                "Approval 4471 approved.",
                status="approved",
                approval_id="4471",
            ),
            _msg("execution_result", "Approval 4471 executed.", status="consumed"),
        ]
    )

    assert history == [
        {"role": "assistant", "content": "Proposed PO #4471."},
        {"role": "assistant", "content": "Approval 4471 approved."},
        {"role": "assistant", "content": "Approval 4471 executed."},
    ]


def test_skips_empty_content() -> None:
    assert build_history([_msg("agent_answer", "   "), _msg("user", "real")]) == [
        {"role": "user", "content": "real"}
    ]


def test_orders_by_input_order_not_created_at() -> None:
    # The store returns messages already ordered by seq; build_history preserves that order.
    msgs = [
        _msg("user", "first"),
        _msg("agent_answer", "second"),
        _msg("user", "third"),
    ]

    history = build_history(msgs)

    assert [m["content"] for m in history] == ["first", "second", "third"]


def test_exclude_turn_id_drops_in_flight_message_even_with_duplicate_content() -> None:
    msgs = [
        _msg("user", "repeat me", turn_id="t0"),
        _msg("agent_answer", "ok", turn_id="t0"),
        _msg("user", "repeat me", turn_id="t1"),
    ]

    history = build_history(msgs, exclude_turn_id="t1")

    assert history == [
        {"role": "user", "content": "repeat me"},
        {"role": "assistant", "content": "ok"},
    ]


def test_window_keeps_last_n_exchanges() -> None:
    msgs = []
    for i in range(4):
        msgs.append(_msg("user", f"q{i}"))
        msgs.append(_msg("agent_answer", f"a{i}"))

    history = build_history(msgs, max_exchanges=2)

    assert [m["content"] for m in history] == ["q2", "a2", "q3", "a3"]


def test_token_budget_drops_oldest_exchanges_but_keeps_at_least_one() -> None:
    big = "x" * 4000
    msgs = [
        _msg("user", "old"),
        _msg("agent_answer", big),
        _msg("user", "new"),
        _msg("agent_answer", "short"),
    ]

    history = build_history(msgs, max_exchanges=10, token_budget=300)

    assert [m["content"] for m in history] == ["new", "short"]


def test_empty_input_returns_empty() -> None:
    assert build_history([]) == []


def test_take_last_exchanges_trims_role_dict_list() -> None:
    history = [
        {"role": "user", "content": "q0"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]

    assert take_last_exchanges(history, 1) == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]


def test_default_constants_are_sane() -> None:
    assert AGENT_HISTORY_MAX_EXCHANGES >= ROUTER_HISTORY_MAX_EXCHANGES >= 1
