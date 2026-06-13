# M4 Slice 6 — Answer Grounding & Confidence (Design)

> Status: Draft | Date: 2026-06-13
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md) (§M4 — eval suite:
> answer groundedness; §R9 agent numbers vs authoritative `get_statistics`)
> Research basis: [2026-06-13-feature-gap-analysis.md](2026-06-13-feature-gap-analysis.md) (candidate B —
> "answer grounding + confidence", the recommended next slice)
> Predecessors: slices 1–5 complete; closest kin is
> [Slice 4 — Tool-Choice Eval](2026-06-12-m4-slice4-tool-choice-eval-design.md) (reuses its `fired_tools`
> trace extraction and behavioral stub-tool harness)

## 1. Context & Goal

The product answers analytical questions but does not **show its work**. The 2026 category norm (gap
analysis §2) is that analytics agents attach citations and a confidence/authority signal to every
answer, because "confidently wrong" numbers are the dominant trust-killer — exactly this project's
top-ranked **R9** risk.

This slice makes every analytical answer carry:

1. a deterministic **authority badge** — is the number backed by the canonical source (`get_statistics`),
   legitimately computed (sandbox), or self-computed/unsupported?
2. an expandable **Sources** list — the exact tool calls whose results fed the turn, linked to the trace
   timeline the console already renders;
3. a **groundedness eval** (LLM judge) — the long-deferred 5th eval dimension — measuring whether the
   answer's claims are actually supported by that evidence.

It is the trust foundation under the later proactive-monitor slice (gap analysis candidate A): every
future alert can cite its evidence and authority.

**Key property — no agent/prompt behavior change.** Authority is *derived from which tools fired*
(already recorded in the `TraceRecord`), not from asking the model to rate itself. This is deterministic
and sidesteps the "model grading its own work" failure mode. The trace already captures the evidence;
this slice **projects** it into the answer.

## 2. Architecture

One pure function over the existing per-turn `TraceRecord`, attached at turn completion:

```
agent.astream_events → capture(..., evidence_max_chars=settings.grounding_evidence_max_chars)
  → record (events + answer + bounded evidence on trace spans)
  → build_grounding(record) → Grounding{authority, source refs}
  → lightweight grounding refs attached to the agent_answer / agent_proposal ThreadMessage
  → persisted in Mongo, returned in GET /thread
  → console renders the badge + Sources expander (fetches trace evidence on demand)
  → groundedness eval scores (answer, trace evidence) offline (fake judge) / live (real judge)
```

The unit under test is `build_grounding`: a deterministic, side-effect-free projection of a
`TraceRecord` into a `Grounding`. It depends only on the trace; it can be understood and tested without
the runtime.

## 3. Components

**New**
- `src/ecommerce_agent/grounding/__init__.py`
- `src/ecommerce_agent/grounding/model.py` — `Grounding`, `GroundingSource`, the `Authority` enum.
- `src/ecommerce_agent/grounding/build.py` — `build_grounding(record) -> Grounding` (pure).
- `src/ecommerce_agent/trace/tools.py` — neutral trace helpers shared by runtime and evals
  (`fired_tools`, data-bearing tool classification).
- `src/ecommerce_agent/evals/groundedness.py` — dataset loader, stub-tool runner, LLM judge, scorer,
  report, baseline writer.
- `src/ecommerce_agent/evals/datasets/groundedness.yaml`
- Frontend: a confidence-badge component + a Sources expander (co-located with the existing answer/
  trace components).

**Modified**
- `src/ecommerce_agent/threads/messages.py` — add `grounding: dict | None = None` to `ThreadMessage`.
- `src/ecommerce_agent/sessions/turn.py` — compute grounding and attach it to the answer/proposal.
- `src/ecommerce_agent/trace/capture.py` / `trace/schema.py` / `trace/projection.py` — capture and
  expose a larger bounded `evidence` field for data-bearing spans (§7).
- `src/ecommerce_agent/cli.py` — `eval groundedness` subcommand.
- `src/ecommerce_agent/config.py` — `grounding_evidence_max_chars` (default 2000).
- Frontend answer/thread components + API types.

## 4. Authority taxonomy (deterministic, from fired tools)

`fired_tools` = the set of tool names from `tool_call` events with `phase == "start"` in the record,
deduped in first-seen order. Slice 4 has this helper today in
`evals/tool_choice.py`; this slice moves it to a neutral trace helper (`trace/tools.py`) and updates
slice 4 to import it, so runtime grounding does **not** depend on eval-only code.

`trace/tools.py` owns the data-bearing allowlist:

```python
DATA_BEARING_TOOLS = READ_ONLY_SPRING_TOOLS | {
    STAGE_SALES_ANALYSIS_TOOL_NAME,  # "stage_sales_analysis_inputs"
    "execute",                      # observed DeepAgents code-execution tool name
}
```

DeepAgents filesystem/scaffolding tools (`write_file`, `read_file`, `ls`, `edit_file`,
`write_todos`, `task`) are **not** data-bearing sources, even though they emit `tool_call` spans. Viz
tools and `request_approval` are also excluded from source extraction.

`sandbox_evidence_fired` means a completed `execute` tool span has output evidence. Staging alone is
not enough: `stage_sales_analysis_inputs` returns file paths/counts, while the derived forecast/trend
numbers come from the later sandbox Python execution.

| authority | rule (first match wins) | operator reads it as |
|---|---|---|
| `authoritative` | `get_statistics` ∈ `fired_tools` | headline numbers came from the canonical backend — trust them |
| `derived` | `sandbox_evidence_fired` and not `get_statistics` | legitimately *computed* (trend/forecast the backend does not own) — sound method |
| `unverified` | neither of the above, **and** the answer contains a numeric claim (regex over `record.answer`) — i.e. raw-read self-compute or claims with no backing data tool | **caution** — the R9 danger zone |
| `not_applicable` | no data tools fired and no numeric claim (greeting, clarification, pure prose) | no badge rendered |

The badge communicates **basis, not correctness**. `derived` is fully legitimate — it is the *expected*
authority for forecast/trend asks. Whether the claims are *faithful* to the evidence is measured
separately by the eval (§6). The numeric-claim regex matches currency, percentages, and bare
multi-digit / decimal quantities; it only separates `unverified` from `not_applicable` (low stakes,
tunable, test-covered).

## 5. Data model — `Grounding`

```python
class Authority(StrEnum):
    AUTHORITATIVE = "authoritative"
    DERIVED = "derived"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


@dataclass
class GroundingSource:
    span_id: str            # links to the existing trace timeline span (tool_call_id)
    tool_name: str
    args_summary: str | None
    result_summary: str | None


@dataclass
class Grounding:
    authority: Authority
    sources: list[GroundingSource]
    diagnostic: str | None = None
```

- **Sources** are the **allowlisted data-bearing** tool-call spans from `record.events` (`tool_call`,
  `phase == "end"`): `READ_ONLY_SPRING_TOOLS` (including `get_statistics`),
  `stage_sales_analysis_inputs`, and `execute`. DeepAgents filesystem/scaffolding tools
  (`write_file`, `read_file`, `ls`, `edit_file`, `write_todos`, `task`) are excluded; they can support
  the workflow, but they do not by themselves evidence a business number. Viz tools are excluded (they
  render, they don't evidence a number). `request_approval` is excluded (it is an action, not
  evidence).
- Each source's `span_id` is the existing `tool_call_id`, so the console links straight to the trace
  span it already shows — **no new trace endpoint**.
- `ThreadMessage.grounding` stores **source references only** (`span_id`, `tool_name`,
  `args_summary`, `result_summary`). Full `evidence` is intentionally kept on the trace span, not on
  the thread message, so normal `GET /thread` and `thread.append` SSE frames stay small.
- For the eval judge and the expanded UI, `build_grounding` source refs are joined back to
  `TraceEvent.evidence` via `span_id` from the trace record / trace endpoint.
- Serialized to a dict on `ThreadMessage.grounding`; `not_applicable` answers store
  `{"authority": "not_applicable", "sources": []}` (or `None` — see §8).

## 6. Groundedness eval (LLM judge)

Reuses the slice-3/4 behavioral harness: real model + stub tools, RUN_LIVE_LLM-gated, JSONL baseline
via `run_metadata`.

- **Dataset — `evals/datasets/groundedness.yaml`:** ~8 analyst asks spanning aggregate / forecast /
  lookup families (no expected-answer labels needed — the judge scores faithfulness, not correctness).
- **Runner:** per case, run the stub-tool analyst turn through `capture(..., evidence_max_chars=...)`,
  then `build_grounding(record)` → `(answer, source refs, evidence_by_span)`.
- **Backend posture:** unlike slice 4, forecast/derived cases cannot use `backend=None`, because this
  eval needs the post-choice sandbox calculation output. Use a lightweight `NoOpSandbox`/fake
  DeepAgents backend whose code execution returns canned analysis text for forecast/trend cases. This
  keeps the eval fast/no-Docker while producing the evidence span the judge needs.
- **Judge:** a model call that, given the answer and the source evidence, scores each numeric claim
  `supported | partial | unsupported`, returning structured JSON. A dedicated judge prompt with a strict
  rubric; strict parse.
- **Report — `GroundednessReport`:** `n`, per-case results, and headline
  **`unsupported_claim_rate`** = unsupported claims / total claims. Also `partial_rate`, and a per-authority
  breakdown.
- **Gates:** live safety gate `unsupported_claim_rate == 0`; advisory overall floor (matches slices
  1/3/4). Persists a baseline line.
- **CLI:** `eval groundedness` builds the stub analyst + judge and prints the report, mirroring
  `eval tool-choice`.

## 7. Evidence fidelity

`result_summary` is truncated to 500 chars ([capture.py:10](../src/ecommerce_agent/trace/capture.py#L10)) —
fine for a timeline glance, too thin for the judge to verify claims against `get_statistics` rows or
sandbox output. Capture a **separate, larger-but-bounded `evidence` field** on data-bearing
`tool_call` end events, capped at `grounding_evidence_max_chars` (default **2000**, configurable).

Implementation contract:

```python
async def capture(
    raw_events: AsyncIterator[dict],
    record: TraceRecord,
    *,
    evidence_max_chars: int = 2000,
) -> AsyncIterator[TraceEvent]: ...
```

`run_turn` passes `settings.grounding_evidence_max_chars`; eval harnesses pass the same cap (or the
default in tests). The 500-char display `result_summary` is unchanged.

Evidence stays **off the model context and off the hot streaming/thread path**:

- `TraceEvent.evidence` is persisted in the trace store and exposed by the trace endpoint/projection
  when the operator opens Sources/Trace.
- `ThreadMessage.grounding` stores only source refs and summaries, not full evidence.
- SSE `thread.append` frames therefore carry only the badge + source refs; no 2000-char evidence
  payload is replayed on every thread load.

If `TraceEvent.evidence` is absent (older traces), the eval/UI can fall back to `result_summary` with a
diagnostic flag.

## 8. Error handling

- `build_grounding` is **best-effort** and never aborts the turn — same posture as the existing
  history/trace `try/except` in `run_turn`.
- Fallback **fails closed**: if `record.answer` contains a numeric claim, an exception returns
  `Grounding(UNVERIFIED, [])` with a diagnostic flag (e.g. `grounding_error=true` in the serialized
  payload). Only nonnumeric answers fall back to `Grounding(NOT_APPLICABLE, [])`.
- `not_applicable` with no sources stores `None` on the message (no badge, no payload weight); all other
  classes store the dict.
- Grounding is attached to **both** `agent_answer` and `agent_proposal` (proposals also cite data; they
  will typically read `derived`/`unverified`). Failure-path answer messages (proposal-fetch failures,
  turn errors) carry no grounding.
- Eval judge: strict JSON parse; an unparseable/ambiguous judgment is counted **`unsupported`**
  (conservative) with a diagnostic flag, never crashing the batch.

## 9. Testing (TDD)

- **Offline `build_grounding`:** synthetic `TraceRecord`s → each authority class (`authoritative`,
  `derived`, `unverified`, `not_applicable`); `derived` requires completed `execute` evidence (not
  staging alone); source extraction uses the allowlist and excludes filesystem/scaffolding tools, viz,
  and `request_approval`; `get_statistics` precedence over sandbox; the numeric-claim heuristic
  boundary; malformed records fail closed to `unverified` when numeric claims are present.
- **Offline turn wiring:** an `agent_answer` carries the expected `grounding`; a proposal carries
  grounding; a failure-path message carries none; `ThreadMessage.grounding` does not include full
  evidence.
- **Offline evidence capture:** a data-tool `tool_call` end event carries `evidence` up to the cap;
  display `result_summary` stays ≤ 500; `capture(..., evidence_max_chars=N)` enforces the cap.
- **Offline eval:** report aggregation incl. `unsupported_claim_rate`, `partial_rate`, per-authority
  breakdown, with a **fake judge**; the conservative-parse path (bad JSON → unsupported); forecast cases
  include canned `execute` evidence via the fake backend.
- **Offline CLI dispatch:** `eval groundedness` runs the branch and prints the report (monkeypatched).
- **Live (RUN_LIVE_LLM):** real analyst + real judge over the dataset; assert
  `unsupported_claim_rate == 0` (gate) and the advisory floor; persist a baseline line.
- **Frontend:** badge renders the right class/label per authority; no badge for `not_applicable`; the
  Sources expander lists sources, fetches trace evidence on demand, and links/highlights each trace span.

## 10. File structure

**New**
- `src/ecommerce_agent/grounding/__init__.py`, `model.py`, `build.py`
- `src/ecommerce_agent/trace/tools.py`
- `src/ecommerce_agent/evals/groundedness.py`
- `src/ecommerce_agent/evals/datasets/groundedness.yaml`
- `tests/test_grounding_build.py`, `tests/test_grounding_turn.py`, `tests/test_groundedness_eval.py`
- `tests/integration/test_groundedness_live.py`
- Frontend: confidence-badge + Sources-expander components and their tests

**Modified**
- `src/ecommerce_agent/threads/messages.py` (`grounding` field)
- `src/ecommerce_agent/sessions/turn.py` (attach grounding)
- `src/ecommerce_agent/trace/capture.py`, `trace/schema.py` (`evidence` field),
  `trace/projection.py` (include evidence for trace/Sources endpoint use)
- `src/ecommerce_agent/config.py` (`grounding_evidence_max_chars`)
- `src/ecommerce_agent/cli.py` + `tests/test_cli.py` (`eval groundedness`)
- Frontend answer/thread components + API types + their tests

## 11. Acceptance criteria

1. Every `agent_answer`/`agent_proposal` carries a deterministic `grounding` (or `None` for
   `not_applicable`), computed from the turn's trace with **no agent/prompt behavior change**.
2. Authority follows §4 (`get_statistics` → `authoritative`; completed `execute` evidence →
   `derived`; numeric claim with neither → `unverified`; else `not_applicable`), using the shared
   neutral `fired_tools` helper.
3. `sources` are the allowlisted data-bearing spans (`READ_ONLY_SPRING_TOOLS`,
   `stage_sales_analysis_inputs`, `execute`); filesystem/scaffolding tools, viz, and
   `request_approval` are excluded. Each source links by `span_id` to the existing trace timeline. Full
   bounded `evidence` lives on trace spans, not on `ThreadMessage.grounding`.
4. `build_grounding` is best-effort, never aborts a turn, and fails closed to `unverified` for numeric
   answers when grounding itself errors.
5. The console shows a confidence badge per authority class (none for `not_applicable`) and an
   expandable Sources list that fetches trace evidence on demand and links/highlights trace spans.
6. `eval groundedness` ships: offline report with a fake judge incl. `unsupported_claim_rate`;
   RUN_LIVE_LLM live run asserts `unsupported_claim_rate == 0` + advisory floor and persists a baseline.
7. Default Python suite + scoped ruff pass; frontend tests pass.

## 12. Risks & open decisions

- **R-A: authority ≠ correctness.** A `derived` forecast can still be numerically wrong; the badge states
  *basis* only. Mitigated by the groundedness eval (faithfulness) and explicit badge semantics.
- **R-B: judge reliability.** LLM judges are noisy. Treated as advisory except the strict
  `unsupported_claim_rate == 0` gate; strict parse; conservative on ambiguity (counts as unsupported).
- **R-C: numeric-claim heuristic** may mis-tag a borderline answer (`unverified` vs `not_applicable`).
  Low stakes (badge only), tunable regex, test-covered.
- **R-D: evidence cap.** 2000 chars may truncate very large `get_statistics` payloads; the judge then
  sees partial evidence. Acceptable (bounded context per R2); cap is configurable; the dataset uses asks
  whose canonical payloads fit.
- **R-E: sandbox evidence naming.** The observed DeepAgents code-execution tool name is `execute`; the
  allowlist pins that name in `trace/tools.py`. If a backend emits a different name later, the
  allowlist and tests must be updated deliberately.
- **Decided:** answer-level sources + badge (not per-claim inline citations); deterministic
  authority-based confidence (not model self-rated); groundedness eval included this slice; full console
  surface; grounding applied to proposals too; evidence cap 2000; full evidence stays on trace spans,
  not thread messages.
