# M4 Slice 1 — Eval-Validated Routing Upgrade (Design)

> Status: Draft | Date: 2026-06-11
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) (§M4 — eval suite; §R5 silent eval risk)
> Predecessor: M3 complete (operator console + persisted trace + Ledger theme)

## 1. Context & Goal

M4 (Product Hardening) is broad — RBAC, audit search, prompt/model/tool versioning, an
evaluation suite, provider fallback, packaging. Per the roadmap's R1 mitigation (WIP = 1, thin
slices, depth over breadth), we build **one slice** first and give it its own spec → plan → build
cycle. This is that slice.

The eval suite is the M4 flagship: R5 names "no eval baseline" the *silent* risk that lets
prompt/model tweaks degrade routing and tool choice unnoticed, and R1 calls the eval pass-rate the
"stop-polishing gate." It should exist before we mutate agent behavior further.

This slice makes the eval immediately useful by giving it a real first customer: **replacing the
brittle keyword router with a model-based intent classifier, and proving the swap is an improvement
by baselining the old mechanism first.** That is the "baseline → change → measured delta" story end
to end.

**Goal:** Extract routing into a stable, extensible seam; replace keyword routing with a
registry-driven intent classifier in the product path; and build a routing eval that scores any
router over a labeled (incl. adversarial) dataset, persists a baseline, and reports the
keyword-vs-classifier delta.

## 2. Scope

**In scope**
- A swappable routing seam (`Router` interface, `RouteDecision`).
- An extensible **specialist registry** (descriptors only) shared by runtime and eval.
- `ClassifierRouter`: one constrained, structured model call over the registry, with a safe
  fallback. Wired into the product runtime.
- Removal of keyword routing from the product path (it survives only as the eval baseline).
- A routing eval: labeled YAML dataset, pure scorer, runner, report with N×N confusion, and a
  `compare()` delta. Baseline persistence reusing the existing JSONL writer.
- Shared eval metadata helpers (refactored out of `live_reliability.py`).

**Out of scope (cut lines)**
- No new specialists/agents (the registry holds exactly today's two: `sales-analyst`,
  `order-manager`).
- No multi-turn / context-aware routing — classify on the latest user message only (matches
  today's `_latest_user_text`); conversation memory is a separate later slice.
- No tool-choice, approval-safety, or groundedness eval dimensions — those are slice 2+.
- No separate `router_model` setting (reserved seam, not built).
- No hard CI gate on the live eval; advisory only.
- No reviving the full DeepAgents coordinator agent-hop.

### Prerequisite: DeepSeek model migration (project-wide, tracked separately)

The local `.env` already sets `llm_model = "deepseek-v4-flash"` (verified 2026-06-11), but the
**code default** in [config.py](../src/ecommerce_agent/config.py) is still `"deepseek-chat"`. Per
DeepSeek's changelog, `deepseek-chat` / `deepseek-reasoner` are **legacy aliases slated for
discontinuation on 2026-07-24**, with `deepseek-chat` temporarily mapping to the non-thinking
`deepseek-v4-flash`. That date is ~6 weeks out, so this slice must not deepen the dependency on the
dead alias:
- This slice's classifier is **model-agnostic** (uses `settings.llm_model`), so it does not bake in
  `deepseek-chat` and will follow the project default once migrated.
- Migrating the project default `deepseek-chat → deepseek-v4-flash` affects the whole app (analyst,
  order-manager, live tests) and must run through the §3 dependency-bump reliability harness. It is
  therefore a **tracked prerequisite / sibling change, not part of this slice** — but it should land
  before or alongside it. The routing slice itself is unblocked because it inherits the default.

## 3. Architecture

Today the routing decision is buried in `RoutedSessionAgent.astream_events`, which calls
`_needs_order_manager(text)` (substring keyword match) in
[sessions/factory.py](../src/ecommerce_agent/sessions/factory.py). We lift it into a `routing`
package behind one interface:

```
async Router.route(message: str) -> RouteDecision      # async: the classifier path is a network call
RouteDecision { specialist: str, source: "classifier" | "fallback" | "keyword", reason: str }
```

Routing is **async**. The classifier path makes an LLM call, and routing happens inside
`RoutedSessionAgent.astream_events`, which `run_turn` drives on the event loop
([turn.py](../src/ecommerce_agent/sessions/turn.py)). A synchronous network call there would block the
loop and stall other sessions' SSE streams, so `route()` is `async` and the classifier awaits
`ainvoke`. `KeywordRouter` implements the same async signature but does no I/O and returns
immediately.

`RoutedSessionAgent` holds a `Router` and a `name -> agent` map: it calls `route(text)`, looks up
`agents[decision.specialist]`, and streams. Nothing else about the runtime changes.

Two consequences fall out of the seam:

1. **The eval targets `route()`, not agent internals.** A routing eval case exercises a single
   classification call — not a full agent turn (no MCP pagination, no sandbox). The eval runs in
   seconds. The keyword baseline path is pure-offline/instant; only the classifier path needs a
   live model.
2. **Runtime vs. eval split for keyword logic.** The product path uses `ClassifierRouter` only.
   The `KeywordRouter` keeps the same interface but lives in the routing package solely as the
   eval's baseline comparator — never selected at runtime.

### Extensibility decision

Specialists are **registry descriptors**, not a closed `Literal`. This keeps the system open to the
roadmap's deferred specialists (customer-insight, procurement-planner, catalog-manager) without
rewrites:

- `RouteDecision.specialist` is **always a registered specialist name** — an open registry-key
  string, not a fixed enum. The classifier's `"unsure"` lives only in `ClassifierOutput` (the raw
  model output) and is resolved to the registry default *before* a `RouteDecision` is formed, so
  `"unsure"` never reaches the eval, the confusion matrix, or the `agents` map.
- The classifier prompt is **generated from the registry descriptions** — adding a specialist means
  registering a descriptor with a good description, not editing routing code or the prompt template.
- The fallback is a **designated property**, not a hardcoded name: exactly one specialist is flagged
  `default` (chosen to be the least-privileged, read-only one — today the `sales-analyst`). Routing
  falls back to "the registry's default," so the code never names a specialist.
- The eval is registry-aware: labels are specialist-name strings and the confusion matrix is N×N
  over registered names, so it stays correct as specialists are added.

This registry + cheap classifier is the lightweight counterpart of the dormant `build_coordinator`
seam (which registers sub-agents by name+description and routes via a full model hop with handoff
tools). The registry is the shared substrate: graduate to the full coordinator only when the roster
grows large or routing turns genuinely ambiguous/multi-hop.

To make "shared substrate" literal rather than aspirational, the registry adopts the **canonical
specialist names already used by the coordinator sub-agent descriptors** —
`sales-analyst` and `order-manager` ([agents.py](../src/ecommerce_agent/agents.py)
`sales_analyst_subagent` / `order_manager_subagent`). These exact strings are the registry keys, the
`agents` map keys, the `RouteDecision.specialist` values, the classifier outputs, and the dataset
`expected` labels — one vocabulary across runtime, eval, and the future coordinator.

## 4. Components

### 4.1 Specialist registry — `routing/registry.py`

Descriptor-only (no agent instances) so the eval can use it without constructing agents.

```
Specialist { name: str, description: str, default: bool }

class SpecialistRegistry:
    specialists: list[Specialist]
    def names(self) -> list[str]
    @property
    def default(self) -> Specialist            # the one with default=True
    def describe(self) -> str                    # "<name>: <description>" lines for the prompt
    def is_registered(self, name: str) -> bool

def build_specialist_registry() -> SpecialistRegistry
    # sales-analyst (default=True, read-only), order-manager (approval-only)
```

`build_specialist_registry()` is the single source of truth shared by the runtime and the eval.
Exactly one specialist must be `default`; the constructor raises if zero or more than one is.

### 4.2 Router interface — `routing/router.py`

```
@dataclass
class RouteDecision:
    specialist: str
    source: str            # "classifier" | "fallback" | "keyword"
    reason: str

class Router(Protocol):
    async def route(self, message: str) -> RouteDecision: ...
```

### 4.3 ClassifierRouter — `routing/router.py`

```
class ClassifierOutput(BaseModel):     # pydantic, for structured output
    specialist: str                     # a registered name, or "unsure"
    reason: str

class ClassifierRouter:
    def __init__(self, model, registry): ...
    async def route(self, message: str) -> RouteDecision
```

Behavior:
1. Build the **system instruction** from `get_prompt("router_classifier")` with the registry
   descriptions injected into a `{specialists}` slot (substitution must tolerate literal braces in
   the template — use an explicit replace of the `{specialists}` token, not `str.format`, to avoid
   brace pitfalls). This is *instructions only* — it does not contain the user's message.
2. One **awaited, non-blocking** model call with an explicit two-message structure — the instruction
   as a `SystemMessage` and the **raw latest user text as a `HumanMessage`**:
   `await structured.ainvoke([SystemMessage(system_instruction), HumanMessage(message)])` where
   `structured = model.with_structured_output(ClassifierOutput, method="function_calling")`. The
   message is the human turn the model classifies — *not* concatenated into the instruction string,
   which would otherwise have the model classify static instructions. The model is **already tuned
   for classification** by its builder (see below) — `temperature=0`, non-streaming, small
   `max_tokens` — so the router does not depend on the primary model's streaming/temperature settings.

   **Structured-output method — validated live against `deepseek-v4-flash` (2026-06-11 spike).** The
   stack runs DeepSeek via `ChatOpenAI`. The hypothesized blocker (`parallel_tool_calls`, which
   `with_structured_output(method="function_calling")` binds to `False`) turned out **not** to be a
   problem — V4 accepts that request shape. The **real** blocker is **thinking mode**:
   `deepseek-v4-flash` defaults thinking *on*, and thinking mode rejects the forced `tool_choice`
   structured output uses, returning `400 "Thinking mode does not support this tool_choice"` on *every*
   call. The fix is to **disable thinking** (see the model-wiring note). Spike results:
   - bare / `disabled_params` only → `400` thinking/tool_choice error every time.
   - `extra_body={"thinking": {"type": "disabled"}}` → **works**: valid `ClassifierOutput`, correct
     routing, ~1.5–2.4 s. `disabled_params` proved **unnecessary** (dropped).
   - the thinking flag must ride in **`extra_body`**, not `model_kwargs` (the latter raises
     `TypeError: ... unexpected keyword argument 'thinking'`).

   So `method="function_calling"` is confirmed on V4. Residual method/parse failures still hit the
   step-4 fallback; `ChatDeepSeek` / lenient single-token parse remain reserved downgrades (R-A). The
   build-step-2 spike is retained as a **RUN_LIVE_LLM regression probe** (it catches a future thinking
   default flip or model swap before it silently breaks routing).
3. If the returned `specialist` is registered → `RouteDecision(specialist=name,
   source="classifier", reason=...)`.
4. If it is `"unsure"`, an unregistered name, or the call raises/times out →
   `RouteDecision(specialist=registry.default.name, source="fallback", reason=...)`, logged.
   `route()` never raises.

**Model wiring:** add `get_classifier_model(settings)` to
[models.py](../src/ecommerce_agent/models.py), mirroring the existing `get_summary_model` /
`get_fallback_model` seams but tuned for classification. It is **model-agnostic** — it uses
`settings.llm_model` / `settings.llm_base_url` / `settings.llm_api_key` like the other model
builders, so it inherits whatever model the project is configured for (it does *not* hardcode a model
name). Classification tuning comes from module constants `CLASSIFIER_TEMPERATURE = 0.0`,
`CLASSIFIER_MAX_TOKENS` (small), `CLASSIFIER_TIMEOUT_SECONDS`, `streaming=False`, plus the
spike-validated non-thinking kwarg:
`ChatOpenAI(model=settings.llm_model, timeout=CLASSIFIER_TIMEOUT_SECONDS, temperature=0,
streaming=False, max_tokens=…, extra_body={"thinking": {"type": "disabled"}})`. The `timeout` is
LangChain's documented per-request bound; `ClassifierRouter.route` *also* wraps the await in
`asyncio.wait_for(..., CLASSIFIER_TIMEOUT_SECONDS)` as a hard ceiling. Either timeout raises → step-4
fallback. (Module constants, not new `Settings` fields — `config.py` has no LLM timeout today; these
can graduate to settings later.)

**Why non-thinking is pinned (now: it's required, not just tuning):** the 2026-06-11 spike showed
`deepseek-v4-flash` defaults `thinking` *on*, and thinking mode **rejects** the forced `tool_choice`
that structured output needs — `400` on every call. `extra_body={"thinking": {"type": "disabled"}}`
both fixes that **and** removes the latency/tokens/non-determinism a thinking router would add. The
flag must be `extra_body`, **not** `model_kwargs` (which raises `TypeError`). `disabled_params` for
`parallel_tool_calls` was tried and proven unnecessary — V4 accepts that shape — so it is omitted.

`build_session_runtime` and the live eval build the classifier model via this helper and pass it to
`ClassifierRouter`. We do *not* reuse `get_primary_model` (it hardwires `settings.llm_temperature`
and `streaming=True`, no token cap, no compat kwargs). `settings.router_model` is a reserved future
per-model override, not built here.

Also export `classifier_model_params(settings) -> dict` (`{name, base_url, temperature, max_tokens,
streaming, timeout_seconds}` from those constants) so the routing baseline records the **classifier's
actual params** — including `timeout_seconds`, which directly affects the fallback rate — not the
primary model's. See §5.5.

### 4.4 KeywordRouter — `routing/keyword.py` (eval baseline only)

Ports the existing keyword logic (`_ORDER_MANAGER_KEYWORDS`, substring match) behind the `Router`
interface. It implements the same `async def route` signature but does no I/O and returns
immediately. It maps a keyword hit → `order-manager`, else → `registry.default` (a miss is the normal
negative case, not an error). Every decision carries `source="keyword"`, since the mechanism is the
same regardless of hit or miss. Imported only by the eval.

### 4.5 Factory rewiring — `sessions/factory.py`

- Delete `_ORDER_MANAGER_KEYWORDS` and `_needs_order_manager`.
- `RoutedSessionAgent.__init__(self, *, router: Router, agents: dict[str, Any], default_specialist:
  str)`. `astream_events` computes `decision = await router.route(_latest_user_text(inputs))`, then
  **emits a synthetic route-decision event** (see "Route observability" below) before selecting
  `agents.get(decision.specialist) or agents[default_specialist]` and streaming its events. Passing
  `default_specialist` explicitly is what lets the defensive fallback resolve a missing key without a
  `KeyError` — the constructor previously had no way to know which specialist is the default.
- `build_session_runtime` builds the two agents as today, builds the registry via
  `build_specialist_registry()`, builds `ClassifierRouter(get_classifier_model(settings), registry)`,
  and constructs `RoutedSessionAgent(router=router, agents={"sales-analyst": analyst_agent,
  "order-manager": order_manager_agent}, default_specialist=registry.default.name)`.
- No new config knobs; no keyword logic remains in the product path.

### 4.6 Route observability (trace + log)

`run_turn` builds the trace by running `capture()` over `agent.astream_events(...)`
([turn.py](../src/ecommerce_agent/sessions/turn.py)). Because `RoutedSessionAgent` only *delegates*
to the chosen agent, the routing decision (which specialist, why, classifier vs. fallback) would
otherwise be invisible in the operator trace. To keep the M3 trace honest (UI rule: the console must
show enough provenance to trust the agent), surface it:

- Before delegating, `RoutedSessionAgent` yields a **synthetic raw event**
  `{"event": "on_route_decision", "data": {specialist, source, reason}}`.
- [trace/capture.py](../src/ecommerce_agent/trace/capture.py) `_to_trace_event` maps it to a
  `TraceEvent(event_type="route_decision", name=specialist, phase="end", status="ok",
  result_summary=f"{source}: {reason}")` — reusing existing schema fields, **no schema change**. It is
  emitted as a single `phase="end"` event (not start/end) so the existing projection captures it.
- [trace/projection.py](../src/ecommerce_agent/trace/projection.py): add `route_decision` to
  `_SPAN_EVENT_TYPES`. Because `_merge` only copies `result_summary` on `phase == "end"` (and never
  copies `args_summary` outside `phase == "start"`), the provenance **must** ride in `result_summary`
  with `phase="end"` — packing `source`+`reason` there means the timeline shows the chosen route
  without any `_merge` change. (A phase-less event, or provenance in `args_summary`, would be
  silently dropped — the exact pitfall this avoids.) SSE: optional thin frame, not required here.
- Also log it at INFO (`specialist`, `source`, `reason`) for debugging.

This makes the route visible in both the live timeline and the persisted trace.

## 5. The routing eval

### 5.1 Dataset — `evals/datasets/routing.yaml`

A labeled set with a small typed loader → `RoutingCase { id, prompt, expected, tags }`. `expected`
is a specialist name; `tags` includes `straightforward` / `adversarial` (and finer tags like
`keyword-false-positive` / `keyword-false-negative`). ~10–14 balanced cases. The loader validates
every `expected` is a registered specialist name and fails fast otherwise.

Adversarial cases deliberately expose keyword routing (the demo payoff):

| prompt | expected | why keyword fails |
|---|---|---|
| "analyze why we keep needing to restock electronics" | sales-analyst | `restock` substring hits → false positive → order-manager |
| "show me a report on purchase order volume last quarter" | sales-analyst | `purchase order` substring hits → false positive (note: must use the space form; the hyphenated "purchase-order" would *not* match plain substring routing, so it would not exercise the failure) |
| "we should buy 500 more units of SKU-12 from the cheapest supplier" | order-manager | no keyword → false negative → sales-analyst |
| "stock is low on blue widgets, can you set up a reorder?" | order-manager | `reorder` not in keyword list → false negative |
| "total sales by category last month?" | sales-analyst | straightforward control |
| "create a purchase order for 200 units of SKU-9" | order-manager | straightforward control |

### 5.2 Scorer — `evals/routing.py`

```
@dataclass
class CaseResult:
    case_id: str
    expected: str
    predicted: str
    passed: bool
    source: str
    tags: list[str]

def score_case(decision: RouteDecision, case: RoutingCase) -> CaseResult
```

Pure, no I/O; unit-tested with synthetic decisions.

### 5.3 Runner — `evals/routing.py`

```
async def run_routing_eval(router: Router, cases: list[RoutingCase], *, router_name: str) -> EvalReport
```

Iterates cases, calls `await router.route(case.prompt)`, scores. A per-case exception is caught and
recorded as a failed `CaseResult` with `predicted = ERROR_PREDICTION` (the module sentinel
`"<error>"`); the batch never aborts. Errored cases count toward `n` and against `accuracy` (they are
failures) but are **excluded from the confusion matrix**, which is keyed by registry names only (see
§5.4). Works for any `Router` (keyword, classifier, or a test stub) since it depends only on the
interface.

### 5.4 Report + delta — `evals/routing.py`

```
@dataclass
class EvalReport:
    router_name: str
    n: int
    passed: int
    errors: int                              # cases whose router call raised (predicted = "<error>")
    accuracy: float                          # passed / n  (errored cases count as failures)
    per_tag_accuracy: dict[str, float]
    confusion: dict[str, dict[str, int]]     # expected -> predicted -> count; SCORED cases only
    cases: list[CaseResult]

def compare(baseline: EvalReport, candidate: EvalReport) -> dict
    # overall accuracy delta, adversarial-subset delta (headline), and the list of per-case flips
```

The confusion matrix is a **nested dict** (`expected → predicted → count`), not tuple-keyed: tuple
keys are not JSON-serializable, and the baseline is persisted via `json.dumps` (§5.5,
[trace/jsonl.py](../src/ecommerce_agent/trace/jsonl.py)). It includes only scored cases (those with a
real predicted specialist) and is keyed by registry names, so it stays a clean N×N over the roster as
specialists are added; errored cases are tallied separately in `errors` and still drag down
`accuracy`.

### 5.5 Baseline persistence + shared metadata

Reuse `append_eval_baseline(entry, path)` from
[trace/jsonl.py](../src/ecommerce_agent/trace/jsonl.py). Each run appends one line:
`{timestamp, git_commit, prompt_hash("router_classifier"), model, dependency_versions, router_name,
accuracy, per_tag_accuracy, confusion}` — all JSON-safe (confusion is the nested dict from §5.4).
Regression = compare the latest line to the prior one for the same `router_name`.

Refactor the metadata helpers out of `evals/live_reliability.py` into **`evals/metadata.py`**
(`git_commit()`, `dependency_versions()`, `prompt_hash(name)`, `run_metadata(settings, *, model: dict
| None = None)`), generalize `prompt_hash` to take a prompt name, and update `live_reliability.py` to
import them. One copy, two harnesses (DRY).

**Correct model block:** today's `run_metadata` hard-codes the `model` block from
`settings.llm_temperature` / `settings.llm_model`. That would mis-record the routing baseline, whose
classifier runs at `temperature=0` with a token cap. So `run_metadata` takes an optional `model`
override; the routing eval passes `classifier_model_params(settings)` (from §4.3) so the baseline
records the **classifier's actual params** (`temperature=0`, `max_tokens`, `streaming=False`). The
hero harness keeps passing nothing and records the primary model as before.

### 5.6 Run surfaces + gate philosophy

Advisory, not a hard CI gate (matches the roadmap):
- **Default CI (offline, deterministic):** the keyword baseline runs with no model and asserts its
  known accuracy — a regression guard on the dataset + scorer wiring.
- **RUN_LIVE_LLM-gated integration test:** classifier accuracy over the dataset, asserting two concrete
  conditions. Not in default CI.
  - **Relative (the meaningful gate):** classifier adversarial-subset accuracy is *strictly greater*
    than keyword's adversarial-subset accuracy. This is the real signal — by construction keyword
    scores poorly on the adversarial subset, so a model router that doesn't beat it isn't earning
    its hop.
  - **Absolute floor (sanity, advisory):** classifier overall accuracy ≥ **0.80**. With a ~10–14
    case set this tolerates ~2 misses; the number is pinned here and tunable as the dataset grows.
    It is advisory, not a hard CI gate (the test is RUN_LIVE_LLM-gated).
- **Optional CLI** `ecommerce-agent eval routing` prints the keyword-vs-classifier comparison table.
  Demo affordance; cuttable.

## 6. Data flow

**Runtime:** message → `RoutedSessionAgent.astream_events` → `await router.route(text)` → emit
`route_decision` trace event →
`RouteDecision(specialist, source)` → `agents[specialist]` → stream. `ClassifierRouter` makes one
structured model call; failure/unsure/invalid → registry default (`source="fallback"`).

**Eval:** dataset → for each case `await router.route(prompt)` → `score_case` → aggregate into
`EvalReport` (accuracy, per-tag, confusion) → `compare(keyword, classifier)` delta → console table +
appended baseline line.

## 7. Error handling

- Classifier exception / timeout / invalid name / `"unsure"` → fallback to `registry.default`,
  `source="fallback"`, logged with reason. `route()` never raises. Timeout is enforced two ways: the
  `ChatOpenAI(timeout=...)` per-request bound and an outer `asyncio.wait_for(...,
  CLASSIFIER_TIMEOUT_SECONDS)`; the raised `TimeoutError` is caught by this same fallback path.
- Structured-output parse failure → caught, treated as a classifier failure → fallback.
- Dataset loader → validates each `expected` against the registry; raises a clear error at load
  time on a malformed dataset.
- Eval runner → per-case exceptions recorded as failed cases; batch continues.
- `RoutedSessionAgent` → if `agents` lacks the routed key, fall back to the default specialist's
  agent (defensive; should not happen given registry/agents are built together).

## 8. Testing (TDD)

- Pure functions (`score_case`, `EvalReport` aggregation, `compare`, `registry.describe`,
  `registry.default` invariant) → deterministic offline unit tests.
- `ClassifierRouter` with a **mocked** structured-output model (async, `pytest.mark.asyncio` —
  awaits `route`) → all four outcomes (valid name / `"unsure"` / unregistered name / raises →
  fallback), and `source` labeling. The mock's `ainvoke` is awaited, confirming the non-blocking path.
- `KeywordRouter` → ported behavior tests (the keyword expectations currently asserted via the
  factory move here).
- Default suite: keyword-baseline offline routing eval asserts deterministic accuracy.
- `tests/integration/test_routing_eval_live.py` (RUN_LIVE_LLM): classifier meets the accuracy threshold
  and beats keyword on the adversarial subset.
- Update `tests/test_session_factory.py` for the new router wiring (router selects agent by
  decision; fallback path).
- `evals/metadata.py` helpers → small unit test (`prompt_hash` by name; `run_metadata` honors the
  `model=` override); keep `live_reliability` tests green after the refactor.
- Route observability →
  - `trace/capture.py` maps an `on_route_decision` raw event to a `route_decision` `TraceEvent`;
    `trace/projection.py` surfaces it (a `phase="end"` event whose `result_summary` carries
    `source`+`reason`) in the timeline.
  - **Emission test (the wiring, not just the mapping):** a `run_turn`/factory test with a stub
    classifier confirms `RoutedSessionAgent` actually *yields* `on_route_decision` before delegating,
    so the resulting `TraceRecord` (and `project_timeline`) contains the route event. This guards the
    gap between "capture can map it" and "the runtime emits it."

## 9. File structure

**New**
- `src/ecommerce_agent/routing/__init__.py`
- `src/ecommerce_agent/routing/registry.py`
- `src/ecommerce_agent/routing/router.py`
- `src/ecommerce_agent/routing/keyword.py`
- `src/ecommerce_agent/evals/metadata.py`
- `src/ecommerce_agent/evals/routing.py`
- `src/ecommerce_agent/evals/datasets/routing.yaml`
- `tests/test_routing_registry.py`
- `tests/test_routing_router.py`
- `tests/test_routing_keyword.py`
- `tests/test_routing_eval.py`
- `tests/test_evals_metadata.py`
- `tests/integration/test_routing_eval_live.py`

**Modified**
- `src/ecommerce_agent/sessions/factory.py` (remove keyword logic; router wiring; emit route event)
- `src/ecommerce_agent/models.py` (add `get_classifier_model` + `classifier_model_params`)
- `src/ecommerce_agent/trace/capture.py` (map `on_route_decision` → `route_decision` TraceEvent)
- `src/ecommerce_agent/trace/projection.py` (include `route_decision` in the operator timeline)
- `src/ecommerce_agent/evals/live_reliability.py` (import shared metadata helpers)
- `src/ecommerce_agent/prompts/prompts.yml` (add `router_classifier`)
- `src/ecommerce_agent/cli.py` (optional `eval routing` subcommand)
- `tests/test_session_factory.py` (router wiring)

## 10. Acceptance criteria

1. No keyword logic in the product path; `factory.py` has no `_ORDER_MANAGER_KEYWORDS` /
   `_needs_order_manager`; the runtime routes via `ClassifierRouter`.
2. `ClassifierRouter` routes over the registry in one structured model call; invalid / unsure /
   error → registry default (`source="fallback"`); `route()` is `async` and never raises, and the
   classifier path awaits `ainvoke` (no event-loop blocking).
3. Adding a specialist needs only: a registry descriptor, the built agent registered in the runtime
   `agents` map, and dataset cases — with **no edits to routing logic, the (generated) classifier
   prompt, the scorer/report, or the confusion shape**. (The runtime still requires a real agent
   instance per specialist; a fully descriptor-driven runtime agent registry is out of scope here.)
4. The routing eval emits accuracy + per-tag accuracy + N×N confusion for keyword and classifier,
   persists a baseline line, and `compare()` reports the overall and adversarial-subset deltas.
5. Default suite passes offline (keyword baseline). RUN_LIVE_LLM: the classifier meets the ≥ 0.80 overall
   accuracy floor and *strictly* beats keyword on the adversarial subset.
6. Metadata helpers are shared by both harnesses (no duplication); the routing baseline records the
   classifier's actual params (temp 0, `max_tokens`), and the persisted entry is JSON-safe (nested-dict
   confusion).
7. The route decision (`specialist`, `source`, `reason`) appears as a `route_decision` event in the
   persisted trace and the operator timeline.

## 11. Risks & open decisions

- **R-A: structured-output compatibility on DeepSeek — RESOLVED by the 2026-06-11 live spike.** The
  real blocker was **thinking mode vs. forced `tool_choice`** (`400` every call on `deepseek-v4-flash`),
  *not* `parallel_tool_calls` (V4 accepts that). Fix: `extra_body={"thinking": {"type": "disabled"}}`
  (must be `extra_body`, not `model_kwargs`); `method="function_calling"` then works. The build-step-2
  spike is retained as a **RUN_LIVE_LLM regression probe** (catches a future thinking-default flip or
  model swap). Reserved downgrades if it ever regresses: `ChatDeepSeek` (verify V4 support first) or a
  lenient single-token parse; residual failures still hit the step-4 fallback. *(Plan keeps the
  structured-output call isolated so the method/parse strategy can change without touching callers.)*
- **R-B: classifier latency on the hot path.** One extra constrained call per turn (temp 0, tiny
  tokens). Acceptable; revisit a keyword/heuristic fast-path only if measured latency hurts.
- **R-C: dataset bias.** A hand-written dataset can be tuned to flatter the classifier. Mitigation:
  keep adversarial cases honest (drawn from real keyword failure modes) and treat the baseline as
  advisory, not proof.
- **Open:** keep or cut the `eval routing` CLI in this slice (default: keep, cuttable). All other
  decisions are settled above.

## 12. Build order (for the plan)

0. **(Prerequisite, tracked separately)** project model migration `deepseek-chat → deepseek-v4-flash`
   through the §3 reliability harness — before/alongside this slice (see §2 Prerequisite).
1. `models.py` `get_classifier_model(settings)` + `classifier_model_params(settings)` (temp 0,
   non-streaming, small `max_tokens`, `timeout`, non-thinking `extra_body={"thinking":{"type":
   "disabled"}}`). No `disabled_params` (spike showed it unnecessary).
2. **Live structured-output spike (RUN_LIVE_LLM) — already validated 2026-06-11, keep as a regression
   probe.** `get_classifier_model(...).with_structured_output(ClassifierOutput,
   method="function_calling")` returns a valid `ClassifierOutput` against `deepseek-v4-flash` with
   thinking disabled (~1.5–2.4 s). Codify it as a `RUN_LIVE_LLM` test so a future thinking-default flip
   or model swap is caught before it silently breaks routing.
3. `routing/registry.py` (+ tests) — descriptors, default invariant, `describe()`.
4. `routing/router.py` `RouteDecision` + async `Router` + `ClassifierRouter` (+ mocked-model tests).
5. `routing/keyword.py` `KeywordRouter` (+ ported tests).
6. `evals/metadata.py` refactor out of `live_reliability.py`, with `run_metadata(..., model=...)`
   override (+ keep its tests green).
7. `evals/datasets/routing.yaml` + loader + `RoutingCase` (+ loader-validation tests).
8. `evals/routing.py` scorer / async runner / `EvalReport` (nested-dict confusion) / `compare`
   (+ pure-function tests).
9. Wire `ClassifierRouter` into `sessions/factory.py`; remove keyword logic; await `route` (+ factory
   tests). **Gated by step 2.**
10. Route observability: emit `on_route_decision` in `RoutedSessionAgent`; map it in `trace/capture.py`;
    project it in `trace/projection.py` (+ capture/projection unit tests **and** a `run_turn`/factory
    emission test proving the persisted trace contains the route event).
11. Add `router_classifier` to `prompts.yml`.
12. RUN_LIVE_LLM integration test (classifier vs keyword on the dataset).
13. Optional `eval routing` CLI subcommand.
