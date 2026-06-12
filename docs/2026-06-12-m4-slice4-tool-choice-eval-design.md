# M4 Slice 4 — Tool-Choice Eval (Design)

> Status: Draft | Date: 2026-06-12
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) (§M4 — eval suite; §R9 agent numbers vs authoritative `get_statistics`)
> Predecessors: [Slice 1 — Routing Eval](2026-06-11-m4-routing-eval-design.md), [Slice 2 — Conversation Memory](2026-06-12-m4-slice2-conversation-memory-design.md), [Slice 3 — Eval Expansion](2026-06-12-m4-slice3-eval-expansion-design.md) (all complete)

## 1. Context & Goal

The eval suite now covers routing, multi-turn routing, and approval-safety. This slice adds the third
agent-correctness dimension, **tool choice**, completing the correctness arc (only groundedness — which
needs an LLM judge — remains, and stays deferred).

**Why it matters (R9).** The sales-analyst can answer an aggregate question two ways: call the
authoritative `get_statistics` tool, or recompute the aggregate itself in the sandbox. The second path
produces *self-computed numbers that can disagree with the canonical backend stats* — confidently
wrong figures, the single biggest trust-killer in an analytics demo. The `sales_analyst` prompt already
encodes the contract ([prompts.yml](../src/ecommerce_agent/prompts/prompts.yml)): use `get_statistics`
for backend-computed aggregates ("do not recompute backend aggregates in sandbox code"); use the
sandbox (`stage_sales_analysis_inputs` → `ecommerce_analysis`) for trends/forecasts/correlations the
backend does not own ("for trend/forecast questions, do not call `get_statistics`"); use read tools for
lookups. This slice measures adherence to that contract.

**This slice is eval-only — no runtime behavior change.** It reuses slice 3's behavioral machinery
(real model + stub tools + trace inspection).

## 2. Scope

**In scope**
- A tool-choice dataset of analyst asks, each labeled with an expected tool and forbidden tools.
- A scorer over the turn trace, an aggregating report (headline `aggregate_recompute_rate`), a loader.
- A stub-tool sales-analyst harness (real model, `backend=None`) and a runner with precise
  error semantics (fail-before-choice vs. pass-after-choice).
- A RUN_LIVE_LLM behavioral gate + an `eval tool-choice` CLI subcommand.
- Reuse of `evals/metadata.py`, the JSONL baseline writer, and `trace.capture`.

**Out of scope (cut lines)**
- No groundedness / LLM-judge (later). No answer-correctness scoring — tool choice scores the
  *strategy*, not whether the final number is right.
- No real Spring/MySQL; stub tools only. No real sandbox (Docker) — `backend=None`.
- No runtime/agent/prompt behavior change.
- No new specialists.

## 3. Architecture

The unit under test is one analyst turn. The **tool choice is fully decided at the first decisive tool
call** — `get_statistics` (aggregate path) vs. `stage_sales_analysis_inputs` (sandbox path) vs. a read
tool (lookup). That call lands in the trace *before* any sandbox code execution, so we never need a
working sandbox: we score the decision from the trace, with `backend=None`.

Slice-3 parallel: dataset → per-case stub-tool turn → score from trace → report → baseline line. The
difference is the runner's error semantics (§5.3) and the scoring contract (§4).

## 4. Scoring

```
@dataclass
class ToolChoiceCase:
    id: str
    prompt: str
    expected_tool: str          # must appear in the trace
    forbidden_tools: list[str]  # must not appear
    tags: list[str]             # includes one of: aggregate | forecast | lookup
```

**`fired_tools`** = the set of tool names from `tool_call` events with **`phase == "start"`** in the
turn's `TraceRecord`. Start events (not end) are the signal: a post-choice crash may never emit the
`end` event, but the decisive tool's `start` is already recorded
([capture.py:212](../src/ecommerce_agent/trace/capture.py#L212)).

A case **passes** iff `expected_tool in fired_tools` **and** `forbidden_tools ∩ fired_tools == ∅`.

Case families and their contract (from the `sales_analyst` prompt):

| tag | expected_tool | forbidden_tools |
|---|---|---|
| aggregate | `get_statistics` | `[stage_sales_analysis_inputs]` |
| forecast | `stage_sales_analysis_inputs` | `[get_statistics]` |
| lookup | a read tool (e.g. `product_query`) | `[get_statistics, stage_sales_analysis_inputs]` |

**Headline metric — `aggregate_recompute_rate`:** among `aggregate`-tagged cases, the fraction where a
forbidden tool fired (i.e. the analyst recomputed a backend aggregate in the sandbox instead of calling
`get_statistics`). This is the R9 wrong-direction, mirroring slice 3's `false_proposal_rate`. The live
safety gate is `aggregate_recompute_rate == 0`.

## 5. Components

### 5.1 Dataset — `evals/datasets/tool_choice.yaml`

~9 cases across the three families:

| prompt | expected_tool | forbidden | tag |
|---|---|---|---|
| "what were total sales by category last month?" | get_statistics | [stage_sales_analysis_inputs] | aggregate |
| "which products are my top sellers this year?" | get_statistics | [stage_sales_analysis_inputs] | aggregate |
| "how much inventory do we have on hand right now?" | get_statistics | [stage_sales_analysis_inputs] | aggregate |
| "how many orders did we get last week?" | get_statistics | [stage_sales_analysis_inputs] | aggregate |
| "forecast next month's sales" | stage_sales_analysis_inputs | [get_statistics] | forecast |
| "which categories are trending up or down over the last 6 months?" | stage_sales_analysis_inputs | [get_statistics] | forecast |
| "is there a correlation between price and units sold?" | stage_sales_analysis_inputs | [get_statistics] | forecast |
| "what's the unit cost of SKU-9?" | product_query | [get_statistics, stage_sales_analysis_inputs] | lookup |
| "who supplies SKU-3?" | supplier_query | [get_statistics, stage_sales_analysis_inputs] | lookup |

**Lookup cases use prompts with an *unambiguous* expected read tool** (product identity → `product_query`,
supplier → `supplier_query`). Avoid lookups that could map to two read tools (e.g. "is X low on stock?"
is ambiguous between `inventory_query` and `inventory_low_stock`) — a single `expected_tool` would
false-fail. The lookup family's real signal is the *forbidden* direction anyway: a simple lookup must
not over-reach to `get_statistics` or the sandbox.

The loader validates `expected_tool` is a non-empty string, `forbidden_tools` a list of strings, and
each case carries exactly one family tag.

### 5.2 Stub-tool analyst harness

Build the real analyst via
`build_sales_analyst(get_primary_model(settings), spring_read_tools=<stubs>, staging_tools=<stub>,
viz_tools=[], backend=None)`:
- `get_statistics` stub → canned aggregate payload (e.g. `{"sales_by_category": [...], "total": ...}`).
- `stage_sales_analysis_inputs` stub → canned file-path metadata (the real tool's shape), recording the
  call. No sandbox is run.
- read-tool stubs (`product_query`, `supplier_query`, `inventory_query`, …) → canned rows.
- stub names mirror the real Spring/staging tools; arg schemas are local Pydantic models (slice-3
  pattern). `viz_tools=[]`, `backend=None`.

A `build_stub_sales_analyst_tools()` builder returns the tool list (offline-testable); a thin
`build_stub_sales_analyst(settings)` wires it onto the real model (live only).

### 5.3 Runner — precise error semantics

Per case, run one turn through `capture()` into a `TraceRecord`, **catching any exception but keeping
the partially-captured record** (do not discard on error — unlike slice 3's approval-safety runner).
Then score from `fired_tools` (start events) per §4, and classify:

- **No raise:** score normally.
- **Raised after the decisive correct call** (`expected_tool in fired_tools` and forbidden absent) →
  **pass**, with `post_choice_error = True` recorded for diagnostics.
- **Raised before any decisive call** (`expected_tool not in fired_tools`) → **failure**
  (`errored_before_choice = True`). No strategy was chosen; this is a real miss.

This tolerates *only post-choice* failure (the sandbox-execution step we deliberately don't run), never
a pre-choice error. The forbidden tools are themselves early decisive tools, so a post-choice crash
cannot hide a later forbidden call — the captured trace holds the complete set of decisive calls.

### 5.4 Report — `ToolChoiceReport`

```
@dataclass
class ToolChoiceCaseResult:
    case_id: str
    expected_tool: str
    fired_tools: list[str]
    passed: bool
    tags: list[str]
    raised: bool = False
    post_choice_error: bool = False        # raised after the correct decisive call (passed)
    errored_before_choice: bool = False    # raised before any decisive call (failed)


@dataclass
class ToolChoiceReport:
    n: int
    passed: int
    accuracy: float
    per_tag_accuracy: dict[str, float]
    per_expected_tool_accuracy: dict[str, float]
    aggregate_recompute_rate: float        # headline R9 metric (aggregate subset)
    post_choice_errors: int                # diagnostic; these still passed
    errors_before_choice: int              # genuine failures from early errors
    cases: list[ToolChoiceCaseResult]
```

Persisted to JSONL via `run_metadata(settings, prompt_name="sales_analyst")` + the report fields.

### 5.5 CLI

Extend the `eval` subcommand: `eval tool-choice` builds the stub analyst and prints accuracy,
`aggregate_recompute_rate`, and per-tag accuracy — mirroring `eval routing` / `eval approval-safety`.
Ships this slice.

## 6. Data flow

`tool_choice.yaml` → per case run the stub-tool analyst turn → `capture()` (keep record on error) →
`fired_tools` (start events) → `score_case` → `ToolChoiceReport` → baseline line. The decisive tool
choice is observed regardless of whether a forecast turn's sandbox phase runs.

## 7. Error handling

- Per-case: see §5.3 — pre-choice error = failure; post-choice error = pass + diagnostic flag; the
  batch never aborts.
- Loader validates case shape and the single family tag; fails fast on malformed entries.
- Live layer skips cleanly without `RUN_LIVE_LLM` / `LLM_API_KEY`.

## 8. Testing (TDD)

- **Offline scorer:** `fired_tools` from synthetic `TraceRecord`s (start events only); pass/fail over
  expected/forbidden; the three families; the post-choice-error path (a record with the correct start
  event + a simulated raise → pass + `post_choice_error`); the pre-choice-error path (raise, no
  decisive start event → fail + `errored_before_choice`).
- **Offline report:** aggregation incl. `aggregate_recompute_rate`, `per_expected_tool_accuracy`,
  `post_choice_errors`, `errors_before_choice`.
- **Offline loader:** valid dataset loads, balanced families; malformed entry raises.
- **Offline harness construction:** `build_stub_sales_analyst` wires `backend=None` and a tool set
  including `get_statistics` and `stage_sales_analysis_inputs` (monkeypatched model/builder — no model
  built, no `create_deep_agent`).
- **Offline runner:** a fake analyst that (a) calls `get_statistics` then ends, (b) calls staging then
  raises, (c) raises immediately → asserts pass / pass+post_choice_error / fail+errored_before_choice.
- **Offline CLI dispatch:** monkeypatched `eval tool-choice` runs the branch and prints the report.
- **Live (RUN_LIVE_LLM):** run the analyst over the dataset; assert `aggregate_recompute_rate == 0`
  (safety gate) and overall accuracy ≥ 0.80 (advisory); persist a baseline line.

## 9. File structure

**New**
- `src/ecommerce_agent/evals/tool_choice.py` (dataset loader, stub harness, scorer, runner, report)
- `src/ecommerce_agent/evals/datasets/tool_choice.yaml`
- `tests/test_tool_choice.py`
- `tests/integration/test_tool_choice_live.py`

**Modified**
- `src/ecommerce_agent/cli.py` (`eval tool-choice` subcommand)
- `tests/test_cli.py` (parser + dispatch coverage)

## 10. Acceptance criteria

1. A tool-choice dataset of analyst asks, each with `expected_tool` + `forbidden_tools` + a family tag;
   the loader validates shape and family.
2. The scorer reads `fired_tools` from `tool_call` **start** events; a case passes iff the expected tool
   fired and no forbidden tool fired.
3. Runner error semantics: a pre-choice error is a failure (`errored_before_choice`); a post-choice
   error still passes (`post_choice_error` flag); the batch never aborts and the trace is kept on error.
4. `ToolChoiceReport` emits accuracy, per-tag and per-expected-tool accuracy, `aggregate_recompute_rate`,
   `post_choice_errors`, and `errors_before_choice`, persisting a JSON-safe baseline line.
5. The behavioral eval runs the analyst with stub tools + real model, `backend=None`, no Docker, no
   Spring.
6. RUN_LIVE_LLM: `aggregate_recompute_rate == 0` and overall accuracy ≥ 0.80.
7. `eval tool-choice` CLI subcommand ships. No runtime/agent/prompt change. Groundedness absent.

## 11. Risks & open decisions

- **R-A: stub-tool fidelity.** If stub names/schemas drift from the real tools, the analyst may not
  invoke them, skewing the choice signal. Mitigation: mirror real names; local Pydantic schemas; the
  construction test pins the set; keep stubs minimal. (Same R-B as slice 3.)
- **R-B: a forecast turn may complete without ever executing (no sandbox).** Acceptable by design — we
  score the *choice* (staging called), not the computation. The runner's post-choice-error handling
  (§5.3) covers turns that raise after staging.
- **R-C: dataset bias.** Hand-written asks can flatter the analyst. Mitigation: cases drawn from the
  prompt's own contract boundaries (aggregate vs. trend vs. lookup); treat the baseline as advisory.
- **Decided:** `eval tool-choice` CLI ships this slice; live advisory floor = 0.80 (matches slices
  1/3); Approach A (score the choice, tolerate only post-choice failure) over a fake `NoOpSandbox`.

## 12. Build order (for the plan)

1. `ToolChoiceCase` + loader (+ validation tests).
2. Scorer: `fired_tools` (start events), `score_case`, family contract (+ unit tests incl. post-choice
   and pre-choice error paths).
3. `ToolChoiceReport` + `aggregate`/report function (+ aggregation tests).
4. `tool_choice.yaml` dataset (+ loader-over-dataset test).
5. Stub-tool analyst harness: `build_stub_sales_analyst_tools` + `build_stub_sales_analyst` (+ offline
   construction test, monkeypatched).
6. Runner `run_tool_choice_eval` with §5.3 error semantics (+ fake-analyst offline tests for the three
   outcomes).
7. RUN_LIVE_LLM integration test (`aggregate_recompute_rate == 0`, accuracy ≥ 0.80, persist baseline).
8. `eval tool-choice` CLI subcommand (+ parser + dispatch tests).
9. Full-suite + scoped ruff verification.
