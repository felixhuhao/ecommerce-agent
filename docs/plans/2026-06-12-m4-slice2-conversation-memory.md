# M4 Slice 2 — Within-Session Conversation Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a session multi-turn coherent — each turn's router and chosen specialist see a bounded recent window of the shared session thread, so follow-ups and cross-specialist references resolve.

**Architecture:** A new pure `threads/history.py` maps persisted `ThreadMessage`s → bounded model-message dicts (excluding the in-flight turn by `turn_id`). `run_turn` loads the thread and prepends that history to the current message. `Router.route` gains an additive `history` param; `ClassifierRouter` folds a tighter recent window into its single existing classifier call as role-preserving prior messages (injection-safe). `RoutedSessionAgent` derives the router window from the assembled messages. Cross-agent memory is free because history is sourced from the session-scoped thread.

**Tech Stack:** Python 3.12, langchain-core messages, pydantic, pytest + pytest-asyncio (`asyncio_mode = "auto"`), the existing `ThreadStore` / `ThreadMessage` / `RoutedSessionAgent` seams.

**Spec:** [docs/2026-06-12-m4-slice2-conversation-memory-design.md](../2026-06-12-m4-slice2-conversation-memory-design.md)

**Conventions for every commit in this plan:**
- Run `uv run pytest <paths> -q` for the cited tests; the default suite is `uv run pytest -q`.
- Each task's `git commit -m "<subject>"` is shorthand. Always append the trailer as a second `-m` so
  the message ends with it:
  `git commit -m "<subject>" -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`
- Commits are **local only** — do not push. Stage only the files each task names; do not `git add -A`
  (the repo has a `.env` and possibly unrelated WIP that must stay untouched).

**Design facts the tasks rely on (verified against current code):**
- `run_turn` does **not** persist the current user message — the API (`api/sessions.py`) appends it
  *before* calling `run_turn` ([sessions.py:314-323](../src/ecommerce_agent/api/sessions.py#L314-L323)),
  and today that message has **no `turn_id`**. So existing `run_turn` tests use an empty store and stay
  green; the dedupe concern only arises in the live API flow.
- `approval_status` / `execution_result` messages already store a compact one-line `content` (e.g.
  "Approval X approved."), so breadcrumbs are just "non-user types → `assistant` role using `content`",
  dropping `card`/`result` payloads.
- `RoutedSessionAgent.astream_events` receives `{"messages": [...]}`; the **last** message is the
  current turn, so history for the router is `messages[:-1]`.

---

### Task 1: History builder module (`threads/history.py`)

**Files:**
- Create: `src/ecommerce_agent/threads/history.py`
- Test: `tests/test_threads_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_threads_history.py`:

```python
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


def test_maps_user_and_agent_messages_to_roles():
    history = build_history(
        [_msg("user", "hello"), _msg("agent_answer", "hi there")]
    )
    assert history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_proposal_and_breadcrumbs_render_as_assistant_content_only():
    history = build_history(
        [
            _msg("agent_proposal", "Proposed PO #4471."),
            _msg("approval_status", "Approval 4471 approved.", status="approved", approval_id="4471"),
            _msg("execution_result", "Approval 4471 executed.", status="consumed"),
        ]
    )
    assert history == [
        {"role": "assistant", "content": "Proposed PO #4471."},
        {"role": "assistant", "content": "Approval 4471 approved."},
        {"role": "assistant", "content": "Approval 4471 executed."},
    ]


def test_skips_empty_content():
    assert build_history([_msg("agent_answer", "   "), _msg("user", "real")]) == [
        {"role": "user", "content": "real"}
    ]


def test_orders_by_input_order_not_created_at():
    # The store returns messages already ordered by seq; build_history preserves that order.
    msgs = [_msg("user", "first"), _msg("agent_answer", "second"), _msg("user", "third")]
    history = build_history(msgs)
    assert [m["content"] for m in history] == ["first", "second", "third"]


def test_exclude_turn_id_drops_in_flight_message_even_with_duplicate_content():
    msgs = [
        _msg("user", "repeat me", turn_id="t0"),
        _msg("agent_answer", "ok", turn_id="t0"),
        _msg("user", "repeat me", turn_id="t1"),  # the in-flight current message
    ]
    history = build_history(msgs, exclude_turn_id="t1")
    # The t1 user message is gone; the identical t0 user message survives (exclude by id, not content).
    assert history == [
        {"role": "user", "content": "repeat me"},
        {"role": "assistant", "content": "ok"},
    ]


def test_window_keeps_last_n_exchanges():
    msgs = []
    for i in range(4):
        msgs.append(_msg("user", f"q{i}"))
        msgs.append(_msg("agent_answer", f"a{i}"))
    history = build_history(msgs, max_exchanges=2)
    assert [m["content"] for m in history] == ["q2", "a2", "q3", "a3"]


def test_token_budget_drops_oldest_exchanges_but_keeps_at_least_one():
    big = "x" * 4000  # ~1000 estimated tokens at 4 chars/token
    msgs = [
        _msg("user", "old"),
        _msg("agent_answer", big),
        _msg("user", "new"),
        _msg("agent_answer", "short"),
    ]
    history = build_history(msgs, max_exchanges=10, token_budget=300)
    # Oldest (huge) exchange dropped to fit the budget; newest exchange kept.
    assert [m["content"] for m in history] == ["new", "short"]


def test_empty_input_returns_empty():
    assert build_history([]) == []


def test_take_last_exchanges_trims_role_dict_list():
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


def test_default_constants_are_sane():
    assert AGENT_HISTORY_MAX_EXCHANGES >= ROUTER_HISTORY_MAX_EXCHANGES >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_threads_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ecommerce_agent.threads.history'`.

- [ ] **Step 3: Write the implementation**

Create `src/ecommerce_agent/threads/history.py`:

```python
from __future__ import annotations

from collections.abc import Sequence

from ecommerce_agent.threads.messages import ThreadMessage

# Bounds are counted in *exchanges* (one user turn + the assistant/proposal/breadcrumb
# messages that follow it), never in raw ThreadMessages, so breadcrumb-heavy turns do not
# consume the window faster than a plain answer turn. Module constants for now; these can
# graduate to Settings later (mirrors slice 1's classifier constants).
AGENT_HISTORY_MAX_EXCHANGES = 6
AGENT_HISTORY_TOKEN_BUDGET = 2000
ROUTER_HISTORY_MAX_EXCHANGES = 3

_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _to_role_message(message: ThreadMessage) -> dict | None:
    """Map a persisted message to a model message, or None to skip it.

    Only the conversational outcome carries forward: user text and assistant text
    (answers, proposals, and compact status/execution breadcrumbs). The full approval
    `card` and `result` payloads are deliberately dropped — the breadcrumb is the stored
    one-line `content`.
    """
    content = (message.content or "").strip()
    if not content:
        return None
    if message.type == "user":
        return {"role": "user", "content": content}
    # agent_answer, agent_proposal, approval_status, execution_result -> assistant.
    return {"role": "assistant", "content": content}


def _group_into_exchanges(messages: list[dict]) -> list[list[dict]]:
    """Group a flat role-dict list into exchanges; a 'user' message starts a new one."""
    groups: list[list[dict]] = []
    current: list[dict] = []
    for message in messages:
        if message["role"] == "user" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    return groups


def _exchanges_tokens(exchanges: list[list[dict]]) -> int:
    return sum(_estimate_tokens(m["content"]) for group in exchanges for m in group)


def build_history(
    messages: Sequence[ThreadMessage],
    *,
    max_exchanges: int = AGENT_HISTORY_MAX_EXCHANGES,
    token_budget: int = AGENT_HISTORY_TOKEN_BUDGET,
    exclude_turn_id: str | None = None,
) -> list[dict]:
    """Build a bounded model-message history from persisted thread messages.

    `messages` is assumed already ordered by `seq` (as ThreadStore.list_messages returns).
    `exclude_turn_id` drops the in-flight turn's message(s) by id — never by content.
    """
    mapped: list[dict] = []
    for message in messages:
        if exclude_turn_id is not None and message.turn_id == exclude_turn_id:
            continue
        role_message = _to_role_message(message)
        if role_message is not None:
            mapped.append(role_message)

    exchanges = _group_into_exchanges(mapped)
    if max_exchanges >= 0:
        exchanges = exchanges[-max_exchanges:]
    while len(exchanges) > 1 and _exchanges_tokens(exchanges) > token_budget:
        exchanges = exchanges[1:]
    return [message for group in exchanges for message in group]


def take_last_exchanges(history: list[dict], max_exchanges: int) -> list[dict]:
    """Trim an already-mapped role-dict history to its last `max_exchanges` exchanges."""
    groups = _group_into_exchanges(history)
    return [message for group in groups[-max_exchanges:] for message in group]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_threads_history.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/threads/history.py tests/test_threads_history.py
git commit -m "feat(memory): add bounded conversation history builder"
```

---

### Task 2: Stamp `turn_id` on the persisted user message (`api/sessions.py`)

This gives the in-flight user message a reliable exclusion key for `build_history`, and is correct for
the audit/correlation spine regardless. Small and isolated; land it before turn assembly relies on it.

**Files:**
- Modify: `src/ecommerce_agent/api/sessions.py:314-323`
- Test: `tests/test_sessions_api.py:136-150`

- [ ] **Step 1: Update the existing assertion to the new (failing) expectation**

The existing test `test_message_runs_turn_and_thread_reload_shows_it`
([tests/test_sessions_api.py:136](../../tests/test_sessions_api.py#L136)) already posts a message,
captures `turn_id` from the response, reloads the thread, and **currently asserts the user message's
`turn_id` is `None`** (line 147). That assertion is exactly what changes. The POST response already
returns `turn_id` (line 142: `turn_id = post.json()["turn_id"]`), so fold the new check in by
replacing line 147:

```python
        assert thread["messages"][0]["turn_id"] is None
```

with:

```python
        assert thread["messages"][0]["turn_id"] == turn_id
```

Do **not** add a new test — this is the right target, and it keeps the assertion that the user message
correlates to the turn that produced the answer (the same `turn_id` already asserted on the trace at
line 150). The harness is the sync `TestClient(build_test_app())` / `/api/sessions/...` flow used
throughout this file; no new fixture is needed.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_sessions_api.py::test_message_runs_turn_and_thread_reload_shows_it -q`
Expected: FAIL — the stored user message's `turn_id` is still `None`, so `== turn_id` fails.

- [ ] **Step 3: Make the change**

In `src/ecommerce_agent/api/sessions.py`, the `turn_id` is generated at line 311
(`turn_id = uuid.uuid4().hex`) just above the append. Add `turn_id=turn_id` to the user
`ThreadMessage`:

```python
        user_message = await append_and_publish(
            store,
            bus,
            ThreadMessage(
                session_id=session_id,
                type="user",
                content=payload.message,
                actor_id="operator",
                turn_id=turn_id,
            ),
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_sessions_api.py -q`
Expected: PASS (no regressions in the file).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/api/sessions.py tests/test_sessions_api.py
git commit -m "feat(memory): stamp turn_id on persisted user message"
```

---

### Task 3: `Router.route` gains additive `history`; `KeywordRouter` accepts and ignores it

**Files:**
- Modify: `src/ecommerce_agent/routing/router.py` (Protocol signature only)
- Modify: `src/ecommerce_agent/routing/keyword.py`
- Test: `tests/test_routing_keyword.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routing_keyword.py`:

```python
@pytest.mark.asyncio
async def test_keyword_router_accepts_and_ignores_history():
    decision = await _router().route(
        "Forecast next month sales by category",
        history=[{"role": "user", "content": "earlier create a purchase order"}],
    )
    # History is ignored by the deterministic baseline: still routes by the latest message only.
    assert decision.specialist == "sales-analyst"
    assert decision.source == "keyword"
```

> The helper `_router()` already exists at the top of this file (returns
> `KeywordRouter(build_specialist_registry())`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_routing_keyword.py -q`
Expected: FAIL — `TypeError: route() got an unexpected keyword argument 'history'`.

- [ ] **Step 3: Make the change**

In `src/ecommerce_agent/routing/router.py`, update the `Router` Protocol (add the import too):

```python
from collections.abc import Sequence
```

```python
class Router(Protocol):
    async def route(
        self, message: str, *, history: Sequence[dict] = ()
    ) -> RouteDecision: ...
```

In `src/ecommerce_agent/routing/keyword.py`, update `route` to accept and ignore `history`:

```python
    async def route(
        self, message: str, *, history: Sequence[dict] = ()
    ) -> RouteDecision:
        del history  # the deterministic baseline routes on the latest message only
        lowered = message.lower()
```

Add the import at the top of `keyword.py`:

```python
from collections.abc import Sequence
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routing_keyword.py tests/test_routing_eval.py -q`
Expected: PASS — the keyword test passes and slice 1's offline keyword baseline over `routing.yaml`
(in `test_routing_eval.py`) stays green because the eval runner calls `router.route(case.prompt)` with
no `history` (defaults to empty).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/routing/router.py src/ecommerce_agent/routing/keyword.py tests/test_routing_keyword.py
git commit -m "feat(memory): add additive history param to Router seam"
```

---

### Task 4: `ClassifierRouter` folds recent history into its single call (role-preserving)

**Files:**
- Modify: `src/ecommerce_agent/routing/router.py`
- Test: `tests/test_routing_router.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_routing_router.py` (the file already defines `FakeStructured`, `FakeModel`, and
`_router(result=..., exc=...)` from slice 1 — reuse them):

```python
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ecommerce_agent.routing.router import ClassifierOutput, ClassifierRouter
from ecommerce_agent.routing.registry import build_specialist_registry


@pytest.mark.asyncio
async def test_history_is_rendered_as_preceding_role_messages():
    router, model = _router(
        ClassifierOutput(specialist="order-manager", reason="ctx")
    )
    await router.route(
        "yes, do that for 500 units",
        history=[
            {"role": "user", "content": "should we restock SKU-12?"},
            {"role": "assistant", "content": "It is low; I can propose a PO."},
        ],
    )
    sent = model._structured.calls[0]
    # SystemMessage (instruction) first, then the prior turns with roles preserved,
    # then the current message last. Prior user text is a HumanMessage, never elevated
    # into the SystemMessage.
    assert isinstance(sent[0], SystemMessage)
    assert isinstance(sent[1], HumanMessage) and sent[1].content == "should we restock SKU-12?"
    assert isinstance(sent[2], AIMessage) and sent[2].content == "It is low; I can propose a PO."
    assert isinstance(sent[3], HumanMessage) and sent[3].content == "yes, do that for 500 units"
    assert len(sent) == 4


@pytest.mark.asyncio
async def test_empty_history_reproduces_slice1_two_message_shape():
    router, model = _router(ClassifierOutput(specialist="sales-analyst", reason="ok"))
    await router.route("what were sales last month?")
    sent = model._structured.calls[0]
    assert [type(m) for m in sent] == [SystemMessage, HumanMessage]
    assert sent[1].content == "what were sales last month?"


@pytest.mark.asyncio
async def test_router_decision_changes_with_history_present():
    # The concrete R-B guard: a model that routes to order-manager iff a prior turn is
    # present proves the history window actually reaches the classifier call.
    class HistoryAwareStructured:
        def __init__(self):
            self.calls = []

        async def ainvoke(self, messages):
            self.calls.append(messages)
            has_prior = any(isinstance(m, (HumanMessage, AIMessage)) for m in messages[1:-1])
            specialist = "order-manager" if has_prior else "sales-analyst"
            return ClassifierOutput(specialist=specialist, reason="ctx")

    class HistoryAwareModel:
        def __init__(self, structured):
            self._structured = structured

        def with_structured_output(self, schema, *, method=None):
            return self._structured

    registry = build_specialist_registry()
    model = HistoryAwareModel(HistoryAwareStructured())
    router = ClassifierRouter(model, registry)

    without = await router.route("do it")
    with_ctx = await router.route(
        "do it", history=[{"role": "user", "content": "propose a PO for SKU-12"}]
    )
    assert without.specialist == "sales-analyst"
    assert with_ctx.specialist == "order-manager"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routing_router.py -q`
Expected: FAIL — `route()` does not accept `history`, and the call contains only 2 messages.

- [ ] **Step 3: Make the change**

In `src/ecommerce_agent/routing/router.py`, update the imports and `ClassifierRouter.route`:

```python
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
```

```python
from ecommerce_agent.threads.history import ROUTER_HISTORY_MAX_EXCHANGES, take_last_exchanges
```

```python
    async def route(
        self, message: str, *, history: Sequence[dict] = ()
    ) -> RouteDecision:
        instruction = get_prompt("router_classifier").replace(
            "{specialists}", self._registry.describe()
        )
        structured = self._model.with_structured_output(
            ClassifierOutput, method=CLASSIFIER_STRUCTURED_OUTPUT_METHOD
        )
        # Recent conversation is untrusted data: render it as preceding role-preserving
        # messages (the instruction stays the only SystemMessage), never folded into the
        # instruction text. Bounded to a tight router window so latency stays flat.
        messages = [SystemMessage(content=instruction)]
        messages.extend(_history_to_messages(history))
        messages.append(HumanMessage(content=message))
        try:
            out = await asyncio.wait_for(
                structured.ainvoke(messages),
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
```

Add the module-level helper (below the class, or above it):

```python
def _history_to_messages(history: Sequence[dict]) -> list[Any]:
    """Render a recent, bounded history window as role-preserving chat messages."""
    windowed = take_last_exchanges(list(history), ROUTER_HISTORY_MAX_EXCHANGES)
    rendered: list[Any] = []
    for item in windowed:
        content = item.get("content", "")
        if item.get("role") == "user":
            rendered.append(HumanMessage(content=content))
        else:
            rendered.append(AIMessage(content=content))
    return rendered
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routing_router.py -q`
Expected: PASS (existing slice-1 tests + 3 new tests).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/routing/router.py tests/test_routing_router.py
git commit -m "feat(memory): classifier router folds recent history into its single call"
```

---

### Task 5: `run_turn` loads thread history and assembles it into `inputs`

**Files:**
- Modify: `src/ecommerce_agent/sessions/turn.py`
- Test: `tests/test_session_turn.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_turn.py` (the file already imports `InMemoryThreadStore`, `SessionBus`,
`run_turn`, `SimpleNamespace`, `AsyncIterator`, `pytest`):

```python
from ecommerce_agent.threads.messages import ThreadMessage


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

    contents = [m["content"] for m in agent.seen_inputs["messages"]]
    assert contents == ["prior q", "prior a", "follow up"]


@pytest.mark.asyncio
async def test_run_turn_excludes_in_flight_user_message_by_turn_id() -> None:
    # Simulate the API having already persisted the current user message with this turn_id.
    store = InMemoryThreadStore()
    await store.append(ThreadMessage(session_id="s1", type="user", content="same text", turn_id="t1"))
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

    contents = [m["content"] for m in agent.seen_inputs["messages"]]
    assert contents == ["same text"]  # exactly one copy, even though content is identical


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

    # History failed, but the turn still ran on the single-message input and completed.
    assert [m["content"] for m in agent.seen_inputs["messages"]] == ["hello"]
    assert record.answer == "answer"
```

> **Note:** the point is that a memory failure does not crash the turn and does not route through
> `run_turn`'s generic failure path. `FailingListStore` only breaks `list_messages`; `append` still
> works, so the answer is recorded normally and `record.answer == "answer"`. The `RecordingAgent`
> (defined in Task 5 Step 1) yields a single chunk with content `"answer"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session_turn.py -q`
Expected: FAIL — `run_turn` still builds a single-message input ignoring the store, so the prepend and
exclude assertions fail.

- [ ] **Step 3: Make the change**

In `src/ecommerce_agent/sessions/turn.py`, add the import:

```python
from ecommerce_agent.threads.history import build_history
```

Replace the input construction in `run_turn` (currently
`inputs = {"messages": [{"role": "user", "content": message}]}` at line 177):

```python
    record = TraceRecord(session_id=session_id, turn_id=turn_id)
    history: list[dict] = []
    try:
        prior = await store.list_messages(session_id)
        history = build_history(prior, exclude_turn_id=turn_id)
    except Exception:  # noqa: BLE001 - a memory failure must never abort the turn.
        logger.warning(
            "history load failed for session %s; continuing single-message", session_id, exc_info=True
        )
        history = []
    inputs = {"messages": [*history, {"role": "user", "content": message}]}
    config = {"recursion_limit": recursion_limit}
```

The local `try/except` is what keeps a memory failure from surfacing into `run_turn`'s broad
turn-failure path (which would append the generic "Sorry, I could not complete that request" message).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session_turn.py -q`
Expected: PASS — new tests pass and the existing `run_turn` tests stay green (they use an empty store,
so `history == []` and the input shape is unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/turn.py tests/test_session_turn.py
git commit -m "feat(memory): run_turn loads bounded thread history into agent input"
```

---

### Task 6: `RoutedSessionAgent` routes with the recent window (+ cross-agent memory)

**Files:**
- Modify: `src/ecommerce_agent/sessions/factory.py`
- Test: `tests/test_session_factory.py`, `tests/test_session_turn.py`

- [ ] **Step 1: Update the stub routers to accept `history`, then add the new tests**

The `RoutedSessionAgent` will call `router.route(text, history=...)`, so the existing test stubs must
accept the kwarg. Update **both** stub routers:

In `tests/test_session_factory.py`, change `StubRouter.route` (line 26) to record history and accept it:

```python
    async def route(self, message: str, *, history=()):
        self.seen.append(message)
        self.seen_history = list(history)
        return RouteDecision(self._specialist, "classifier", "r")
```

(Add `self.seen_history: list = []` in `StubRouter.__init__`.)

In `tests/test_session_turn.py`, change the inline `StubRouter.route` inside
`test_run_turn_records_route_decision_event` (line 152) to accept history:

```python
        async def route(self, message: str, *, history=()) -> RouteDecision:
            assert message == "what were sales last month?"
            return RouteDecision("sales-analyst", "classifier", "analytics")
```

Now add a router-window test to `tests/test_session_factory.py`:

```python
@pytest.mark.asyncio
async def test_routed_agent_passes_recent_history_to_router() -> None:
    router = StubRouter("order-manager")
    routed = RoutedSessionAgent(
        router=router, agents=_agents(), default_specialist="sales-analyst"
    )

    messages = [
        {"role": "user", "content": "how are electronics selling?"},
        {"role": "assistant", "content": "Down 12% this month."},
        {"role": "user", "content": "restock the worst performer"},
    ]
    _ = [e async for e in routed.astream_events({"messages": messages}, config={}, version="v2")]

    # Router classifies the latest message and sees the prior turns as history (not the latest).
    assert router.seen == ["restock the worst performer"]
    assert {"role": "assistant", "content": "Down 12% this month."} in router.seen_history
    assert {"role": "user", "content": "restock the worst performer"} not in router.seen_history
```

And add a cross-agent memory test to `tests/test_session_turn.py` (uses the real `RoutedSessionAgent`
+ a real `ClassifierRouter`-shaped stub, driving `run_turn` end to end so history comes from the store):

```python
@pytest.mark.asyncio
async def test_cross_agent_memory_order_manager_sees_prior_analyst_answer() -> None:
    store = InMemoryThreadStore()
    # A prior sales-analyst turn established a fact in the shared thread.
    await store.append(
        ThreadMessage(session_id="s1", type="user", content="how are electronics?", turn_id="t0")
    )
    await store.append(
        ThreadMessage(
            session_id="s1", type="agent_answer", content="Electronics are the worst performer.", turn_id="t0"
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

    contents = [m["content"] for m in order_manager.seen_inputs["messages"]]
    assert "Electronics are the worst performer." in contents
    assert contents[-1] == "restock the worst performer"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_session_factory.py tests/test_session_turn.py -q`
Expected: FAIL — `RoutedSessionAgent` does not yet pass `history` to the router (the new factory test
sees empty `seen_history`); the cross-agent test passes the analyst answer through but the router-call
path is unchanged.

- [ ] **Step 3: Make the change**

In `src/ecommerce_agent/sessions/factory.py`, add the import:

```python
from ecommerce_agent.threads.history import ROUTER_HISTORY_MAX_EXCHANGES, take_last_exchanges
```

In `RoutedSessionAgent.astream_events`, compute the recent window (the messages before the current
turn) and pass it to the router:

```python
    async def astream_events(
        self,
        inputs: dict,
        *,
        config: dict,
        version: str,
    ) -> AsyncIterator[dict]:
        messages = inputs.get("messages") or []
        text = _latest_user_text(inputs)
        history = take_last_exchanges(list(messages[:-1]), ROUTER_HISTORY_MAX_EXCHANGES)
        decision = await self.router.route(text, history=history)
        logger.info(
            "route decision: specialist=%s source=%s reason=%s",
            decision.specialist,
            decision.source,
            decision.reason,
        )
        yield {
            "event": "on_route_decision",
            "data": {
                "specialist": decision.specialist,
                "source": decision.source,
                "reason": decision.reason,
            },
        }
        selected = self.agents.get(decision.specialist) or self.agents[self.default_specialist]
        async for event in selected.astream_events(inputs, config=config, version=version):
            yield event
```

`messages[:-1]` is the history (the last message is the current turn). When `messages` has a single
entry (the existing factory tests), the window is empty and behavior matches slice 1.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_session_factory.py tests/test_session_turn.py -q`
Expected: PASS — including the existing `test_routed_session_agent_*` tests (single-message inputs →
empty history) and the new router-window + cross-agent tests.

- [ ] **Step 5: Commit**

```bash
git add src/ecommerce_agent/sessions/factory.py tests/test_session_factory.py tests/test_session_turn.py
git commit -m "feat(memory): route with recent window; enable cross-agent memory"
```

---

### Task 7: Full-suite verification

- [ ] **Step 1: Run the default suite**

Run: `uv run pytest -q`
Expected: PASS — live/integration tests skip without `RUN_LIVE_LLM` / Docker / Spring. No regressions
in routing, session, turn, factory, or API tests. Confirm slice 1's offline keyword baseline
(`test_routing_eval.py`) is still green under the new `history` signature.

- [ ] **Step 2: Run ruff (lint repo-wide; format-check only this slice's files)**

`ruff check` is clean repo-wide today, so lint the whole tree:

Run: `uv run ruff check src tests`
Expected: `All checks passed!`.

**Do not** run `ruff format --check src tests` repo-wide — 8 files have **pre-existing** format drift
unrelated to this slice (`api/app.py`, `tools/__init__.py`, `tools/staging.py`, `tests/integration/*`,
`test_staging_tool.py`, and the two this slice also touches). Reformatting them would add unrelated
churn. Scope the format check to the files this slice creates or edits:

Run:
```bash
uv run ruff format --check \
  src/ecommerce_agent/threads/history.py \
  src/ecommerce_agent/api/sessions.py \
  src/ecommerce_agent/routing/router.py \
  src/ecommerce_agent/routing/keyword.py \
  src/ecommerce_agent/sessions/turn.py \
  src/ecommerce_agent/sessions/factory.py \
  tests/test_threads_history.py \
  tests/test_sessions_api.py \
  tests/test_routing_keyword.py \
  tests/test_routing_router.py \
  tests/test_session_turn.py \
  tests/test_session_factory.py
```
Expected: clean. If any touched file is flagged, run `uv run ruff format <that file>` and re-check.
Note: `api/sessions.py` and `test_sessions_api.py` already carried pre-existing drift; running
`ruff format` on them will also tidy that drift — acceptable since the slice edits them anyway. Leave
the untouched 6 drifted files alone (out of scope for this slice).

- [ ] **Step 3 (optional, recommended): live two-turn coherence smoke**

If credentials are available, manually verify a two-turn follow-up routes coherently with context (no
automated live test is required by this slice — the deterministic R-B guard in Task 4 covers the
regression surface). Example: turn 1 "how are electronics selling?" (→ sales-analyst), turn 2 "create a
PO to restock the worst performer" (→ order-manager, resolving "worst performer" from history).

---

## Self-Review

**Spec coverage** (against [the spec](../2026-06-12-m4-slice2-conversation-memory-design.md)):
- §3.1 history builder (mapping, `exclude_turn_id`, exchange-counted window + token budget,
  breadcrumbs as content-only) → Task 1.
- §3.2 turn assembly + `api/sessions.py` `turn_id` stamp + local try/except fallback → Tasks 2, 5.
- §3.3 context-aware router (additive `history`, role-preserving injection-safe render, tighter
  window) → Tasks 3, 4; `KeywordRouter` parity → Task 3.
- §3.4 bounds constants (`AGENT_HISTORY_MAX_EXCHANGES`/`_TOKEN_BUDGET`, `ROUTER_HISTORY_MAX_EXCHANGES`)
  → Task 1.
- §3 cross-agent memory (shared session thread) → Task 6 cross-agent test.
- §6 error handling: load failure → single-message (Task 5); router history failure → existing
  fallback (unchanged, covered by slice-1 tests); current-message exclusion by id (Tasks 1, 5).
- §7 testing: build_history units (Task 1); run_turn prior-turn/single-copy/load-failure (Task 5);
  context-aware classifier + empty-history slice-1 shape + R-B guard (Task 4); KeywordRouter ignores
  history (Task 3); cross-agent (Task 6); routing regression guard = R-B deterministic test (Task 4) +
  green keyword baseline (Tasks 3, 7).
- §9 acceptance 1–7 → Tasks 5, 4, 6, 1, 5, 3/7, (out-of-scope items absent by construction).

**Placeholder scan:** every code step contains full code. The only adapt-to-existing points are
explicitly flagged: Task 2 reuses `test_sessions_api.py`'s existing fixture/session-creation flow, and
Task 5's load-failure test has a documented simplification. Both name exactly what to mirror.

**Type consistency:** `build_history(messages, *, max_exchanges, token_budget, exclude_turn_id)`,
`take_last_exchanges(history, max_exchanges)`, constants `AGENT_HISTORY_MAX_EXCHANGES` /
`AGENT_HISTORY_TOKEN_BUDGET` / `ROUTER_HISTORY_MAX_EXCHANGES`, `Router.route(message, *, history=())`,
`RouteDecision(specialist, source, reason)`, role dicts `{"role": "user"|"assistant", "content": str}`,
canonical names `sales-analyst` / `order-manager`, and the `messages[:-1]` history convention are used
consistently across Tasks 1–6.
