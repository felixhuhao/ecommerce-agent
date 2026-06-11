# M4 Slice 2 — Within-Session Conversation Memory (Design)

> Status: Draft | Date: 2026-06-12
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) (§M4 — optional memory; §R2 latency/token bloat; §R5 silent eval risk)
> Predecessor: [M4 Slice 1 — Eval-Validated Routing Upgrade](2026-06-11-m4-routing-eval-design.md) (complete)

## 1. Context & Goal

M4 (Product Hardening) is broad. Per the roadmap's R1 mitigation (WIP = 1, thin slices, depth over
breadth), we build one slice at a time with its own spec → plan → build cycle. Slice 1 shipped the
eval flagship and used it to replace keyword routing with a `ClassifierRouter`. This is slice 2.

**Why memory before the next eval dimension.** Slice 1's eval was impressive because it had a
*customer* — it validated a real behavior change (keyword → classifier) with a measured delta. A
second eval dimension built right now would have no behavior change to validate; that is the shape of
R1 ("endless polish that never reaches the crown jewel"). Conversation memory is the more useful,
more demoable capability, and it becomes the natural customer that makes a *later* eval slice
(multi-turn / context-aware routing eval, slice 3) coherent and motivated — mirroring slice 1's
winning baseline → change → delta structure.

**The gap today.** [sessions/turn.py](../src/ecommerce_agent/sessions/turn.py) builds
`inputs = {"messages": [{"role": "user", "content": message}]}` — only the current message. The agent
is stateless across turns even though [threads/store.py](../src/ecommerce_agent/threads/store.py)
already persists the full session conversation (`user`, `agent_answer`, `agent_proposal`,
`approval_status`, `execution_result`) ordered by `seq`. The data exists; it is never read back into a
turn. The router likewise classifies on the latest message only (`_latest_user_text`).

**Goal:** Make a session multi-turn coherent. Each turn — both the router and the chosen specialist —
sees a bounded recent window of the shared session thread, so follow-ups ("now just electronics",
"yes, do that for 500 units") and cross-specialist references ("restock the worst performer") resolve
correctly. History is sourced from the session-scoped thread, so memory is shared across specialists
for free.

## 2. Scope

**In scope**
- A pure history builder: persisted `ThreadMessage`s → bounded model-message list.
- `run_turn` loads prior thread messages and assembles `{"messages": [...history..., current]}`.
- A **full context-aware router**: `Router.route` gains an additive optional `history` param;
  `ClassifierRouter` folds a tight recent window into its single existing classifier call.
- Cross-agent (cross-specialist) memory within a session — a property of sourcing history from the
  session thread, not a separate mechanism.
- Bounded windows (module constants) for the agent and a tighter one for the router, keeping per-turn
  latency flat regardless of session length.
- TDD coverage incl. a thin routing regression guard.

**Out of scope (cut lines)**
- **Cross-session memory / persistence** of facts or preferences across separate conversations
  (reserved future seam; belongs with the RBAC/audit slice).
- **Summarization / compaction** of older turns (recent window only; rolling-summary memory is its
  own later slice with its own correctness/eval surface).
- The **multi-turn routing eval** — slice 3 owns it (this slice ships the behavior; the eval that
  measures it comes next, per §1).
- Per-specialist **attribution** in the transcript (prior answers render as a plain unattributed
  "assistant"; attribution is an easy later refinement).
- Replaying any agent's **internal tool-call scratchpad** across turns — only the conversational
  outcome carries forward.

## 3. Architecture

History is sourced from the `ThreadStore`, which is keyed by `session_id` (not by specialist). The
thread is one shared conversation log for the whole session. Consequences:

1. **Cross-agent memory is free.** Whichever specialist handles this turn reads the entire session
   transcript, including turns the *other* specialist handled. No separate cross-agent mechanism.
2. **The boundary is conversational, not scratchpad.** We replay final answers + user messages +
   compact event breadcrumbs — not each agent's intra-turn tool calls. Intra-turn agent state stays
   per-turn; the shared thread carries the cross-turn, cross-agent narrative.

```
build_history(messages: list[ThreadMessage], *, budget) -> list[dict]   # pure, no I/O
Router.route(message: str, *, history: Sequence[dict] = ()) -> RouteDecision   # additive param
```

### 3.1 History builder — `threads/history.py`

Pure function, no I/O, fully unit-testable. Maps persisted thread messages to model messages and
applies the recent-window budget.

Mapping:
- `user` → `{"role": "user", "content": ...}`
- `agent_answer`, `agent_proposal` → `{"role": "assistant", "content": ...}` (plain, unattributed)
- `approval_status`, `execution_result` → compact assistant **breadcrumb**, e.g.
  `"(proposed PO #4471 — approved)"` / `"(executed PO #4471 — 200 units)"` — the one-line outcome,
  **not** the full card payload.

Bounding: keep the most recent exchanges within a window/token budget (see §4). Ordering is by `seq`
(never `created_at`). Empty/malformed content is skipped; oversized history is truncated from the
oldest end so the newest turns always survive.

### 3.2 Turn assembly — `sessions/turn.py`

`run_turn` (and/or its caller) loads `await store.list_messages(session_id)`, calls `build_history`,
and assembles `inputs = {"messages": [*history, {"role": "user", "content": message}]}`.

**Append-order invariant:** history is built from messages persisted *before* this turn's answer is
appended. The current user message must not be double-counted — either history is loaded before the
user message is appended, or the builder excludes the in-flight turn. The plan must make this explicit
and test it (no duplicated current message).

### 3.3 Context-aware router — `routing/router.py`

`Router.route` signature gains `*, history: Sequence[dict] = ()`. This is **additive**: slice 1's eval
and its single-message dataset pass no history and behave exactly as before, so they stay green.

`ClassifierRouter.route` renders a **tighter** recent sub-window of `history` into its classifier
input — folded into the **same single call** it already makes (one constrained, non-thinking,
structured call per turn). This is not a new model hop; it lengthens the input of the existing call.
Output stays the tiny `ClassifierOutput {specialist, reason}`. Exact mechanism (preceding context
messages vs. a compact "Recent conversation:" block in the system instruction) is a plan detail; the
contract is "recent context in, same one structured call, same fallback semantics."

`KeywordRouter.route` accepts and ignores `history` (interface parity; deterministic eval baseline
unchanged).

`RoutedSessionAgent` passes the router its window: `await self.router.route(text, history=...)`.

### 3.4 Bounds — module constants

Mirror slice 1's classifier constants (module-level, not new `Settings` fields; can graduate later):
- `AGENT_HISTORY_MAX_EXCHANGES` ≈ 6 and/or `AGENT_HISTORY_TOKEN_BUDGET` ≈ 2000 (whichever binds
  first).
- `ROUTER_HISTORY_MAX_EXCHANGES` ≈ 3 (tighter; the router only needs enough to disambiguate a
  follow-up).

Fixed windows keep per-turn latency and token cost flat as a session grows (R2). The router's added
context is marginal next to the agent's own history (full reasoning + tool results).

## 4. Performance

The context-aware router does **not** add a model call — it fattens the one classifier call already
on the hot path. Input tokens are cheap and barely move latency; the round-trip + (tiny, capped)
output dominate. The only real trap is *unbounded* context growth, which the fixed router window
(§3.4) eliminates: router latency stays flat regardless of conversation length. The agent's bounded
window does the same for the specialist call.

## 5. Data flow

message → `store.list_messages(session_id)` → `build_history` (bounded) →
`RoutedSessionAgent.astream_events` → `router.route(text, history=router_window)` (context-aware,
single call) → `agents[specialist].astream_events({"messages": [*agent_window, current]})` → stream →
answer appended to thread (unchanged path). Cross-specialist references resolve because both
specialists read the same session thread.

## 6. Error handling

- **History load failure / empty thread** → degrade to today's single-message behavior. A memory
  problem must never block a turn.
- **Malformed / oversized messages** → skipped or truncated by the budget; newest turns survive.
- **Router history failure** → router falls back to latest-message-only (the existing slice-1
  fallback path); `route()` still never raises.
- **Current-message duplication** → prevented by the append-order invariant (§3.2) and tested.

## 7. Testing (TDD)

- `build_history` (pure, offline): role mapping; ordering by `seq`; window/token-budget truncation
  (oldest dropped first); breadcrumb rendering for approval/execution events; current-message dedupe;
  empty-thread → empty history.
- `run_turn`: a second turn's assembled `inputs["messages"]` contains the prior turn's user + answer;
  empty/first turn matches today's single-message shape (no regression).
- Context-aware `ClassifierRouter` (mocked structured model): identical latest message routes
  differently given different `history`, proving the window reaches the call; empty history reproduces
  slice-1 behavior. `KeywordRouter` ignores `history`.
- **Cross-agent**: an `order-manager` turn whose history contains a prior `sales-analyst`
  `agent_answer` receives that answer in its `messages` (the "restock the worst performer" path).
- **Routing regression guard** (thin): slice 1's offline keyword baseline over `routing.yaml` still
  passes with the new signature (history defaulted empty). Slice 3 owns the multi-turn eval.
- Live (RUN_LIVE_LLM, optional): a two-turn follow-up routes coherently with context.

## 8. File structure

**New**
- `src/ecommerce_agent/threads/history.py`
- `tests/test_threads_history.py`

**Modified**
- `src/ecommerce_agent/sessions/turn.py` (load thread, assemble history into `inputs`)
- `src/ecommerce_agent/sessions/factory.py` (`RoutedSessionAgent` passes router window; route with
  `history=`)
- `src/ecommerce_agent/routing/router.py` (`Router.route` additive `history`; `ClassifierRouter`
  folds recent window into its single call)
- `src/ecommerce_agent/routing/keyword.py` (accept + ignore `history`)
- `tests/test_session_turn.py`, `tests/test_session_factory.py`, `tests/test_routing_router.py`,
  `tests/test_routing_keyword.py` (signature + behavior coverage)

## 9. Acceptance criteria

1. A turn's agent input includes a bounded recent window of prior session messages; an empty/first
   turn matches today's single-message behavior (no regression).
2. The router is context-aware — `route(message, *, history)` — and `ClassifierRouter` folds a tighter
   recent window into its **single existing** classifier call (no extra model hop); `route()` still
   never raises and slice-1 empty-history behavior is preserved.
3. Cross-specialist memory works: an `order-manager` turn can reference a fact established by a prior
   `sales-analyst` turn in the same session, sourced from the shared thread.
4. Windows are bounded by module constants; per-turn latency/tokens do not grow with session length.
5. History load failure or an empty thread degrades to single-message behavior without failing the
   turn; the current message is never duplicated.
6. Slice 1's eval and offline keyword baseline stay green under the new `history` signature.
7. Cross-session memory, summarization, and the multi-turn routing eval are explicitly **not** present
   (reserved for later slices).

## 10. Risks & open decisions

- **R-A: token/latency bloat (R2).** Mitigated by fixed agent + router windows (§3.4); revisit a
  summary/compaction memory only if real sessions exceed the window meaningfully.
- **R-B: shipping a behavior change before its eval (R5).** Context-aware routing changes routing
  behavior before slice 3's multi-turn eval exists. Mitigations: it is additive and bounded; slice
  1's routing eval still guards the latest-message path; a thin regression guard is in this slice; the
  multi-turn eval is the explicit subject of slice 3.
- **R-C: breadcrumb fidelity.** Compact event breadcrumbs may omit detail a specialist wants (e.g.
  full PO line items). Acceptable for conversational continuity; the specialist can re-read
  authoritative data via tools. Revisit if a flow needs richer carried-over state.
- **Open:** exact router context mechanism (preceding messages vs. system-instruction block) and the
  precise window sizes/budget — pinned in the plan, tunable later; not load-bearing for the design.

## 11. Build order (for the plan)

1. `threads/history.py` `build_history` + bounds constants (+ pure unit tests).
2. `Router.route` additive `history` param; `KeywordRouter` accepts/ignores it (+ signature tests,
   keep slice-1 eval green).
3. `ClassifierRouter` folds the recent window into its single classifier call (+ mocked-model tests
   proving history changes the decision; empty history = slice-1 behavior).
4. `sessions/turn.py` loads the thread and assembles history into `inputs` (+ run_turn tests,
   append-order/dedupe).
5. `RoutedSessionAgent` passes the router window; cross-agent test.
6. Thin routing regression guard; optional RUN_LIVE_LLM two-turn coherence test.
