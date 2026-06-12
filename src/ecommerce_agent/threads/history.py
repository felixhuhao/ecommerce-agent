from __future__ import annotations

import logging
from collections.abc import Sequence

from ecommerce_agent.threads.messages import ThreadMessage

logger = logging.getLogger(__name__)

# Bounds are counted in exchanges (one user turn plus the assistant/proposal/breadcrumb
# messages that follow it), never in raw ThreadMessages, so breadcrumb-heavy turns do not
# consume the window faster than a plain answer turn. Module constants for now; these can
# graduate to Settings later (mirrors slice 1's classifier constants).
AGENT_HISTORY_MAX_EXCHANGES = 6
AGENT_HISTORY_TOKEN_BUDGET = 2000
ROUTER_HISTORY_MAX_EXCHANGES = 3

_CHARS_PER_TOKEN = 4
_ROLE_BY_MESSAGE_TYPE = {
    "user": "user",
    "agent_answer": "assistant",
    "agent_proposal": "assistant",
    "approval_status": "assistant",
    "execution_result": "assistant",
}

RoleMessage = dict[str, str]


def _estimate_tokens(text: str) -> int:
    # Cheap deterministic budget guard; real tokenizer precision is unnecessary here.
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _to_role_message(message: ThreadMessage) -> RoleMessage | None:
    """Map a persisted message to a model message, or None to skip it.

    Only the conversational outcome carries forward: user text and assistant text
    (answers, proposals, and compact status/execution breadcrumbs). The full approval
    `card` and `result` payloads are deliberately dropped; the breadcrumb is the stored
    one-line `content`.
    """
    content = (message.content or "").strip()
    if not content:
        return None
    role = _ROLE_BY_MESSAGE_TYPE.get(message.type)
    if role is None:
        logger.warning("skipping unsupported thread message type in history: %s", message.type)
        return None
    return {"role": role, "content": content}


def _group_into_exchanges(messages: list[RoleMessage]) -> list[list[RoleMessage]]:
    """Group a flat role-dict list into exchanges; a user message starts a new one."""
    groups: list[list[RoleMessage]] = []
    current: list[RoleMessage] = []
    for message in messages:
        if message["role"] != "user" and not current:
            continue
        if message["role"] == "user" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def _exchanges_tokens(exchanges: list[list[RoleMessage]]) -> int:
    return sum(_estimate_tokens(m["content"]) for group in exchanges for m in group)


def build_history(
    messages: Sequence[ThreadMessage],
    *,
    max_exchanges: int = AGENT_HISTORY_MAX_EXCHANGES,
    token_budget: int = AGENT_HISTORY_TOKEN_BUDGET,
    exclude_turn_id: str | None = None,
) -> list[RoleMessage]:
    """Build a bounded model-message history from persisted thread messages.

    `messages` is assumed already ordered by `seq` (as ThreadStore.list_messages returns).
    `exclude_turn_id` drops the in-flight turn's message(s) by id, never by content.
    """
    mapped: list[RoleMessage] = []
    for message in messages:
        if exclude_turn_id is not None and message.turn_id == exclude_turn_id:
            continue
        role_message = _to_role_message(message)
        if role_message is not None:
            mapped.append(role_message)

    exchanges = _group_into_exchanges(mapped)
    if max_exchanges == 0:
        exchanges = []
    elif max_exchanges > 0:
        exchanges = exchanges[-max_exchanges:]
    while len(exchanges) > 1 and _exchanges_tokens(exchanges) > token_budget:
        exchanges = exchanges[1:]
    return [message for group in exchanges for message in group]


def take_last_exchanges(history: list[RoleMessage], max_exchanges: int) -> list[RoleMessage]:
    """Trim an already-mapped role-dict history to its last `max_exchanges` exchanges."""
    if max_exchanges == 0:
        return []
    groups = _group_into_exchanges(history)
    return [message for group in groups[-max_exchanges:] for message in group]
