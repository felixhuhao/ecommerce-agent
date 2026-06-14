# M4 Slice 7 — Proactive Monitor & Anomaly Alerts (Design + Checklist)

> Status: Draft | Date: 2026-06-14
> This is **gap-analysis candidate A** ("proactive monitor"), the slice after slice 6 (grounding = B).
> Process note: per the lightened cadence, this is **one combined doc** — design + an embedded
> implementation checklist — not a separate spec and plan. (Tier-3 novelty, but executed warm.)
> Parent roadmap: [2026-06-09-product-roadmap.md](2026-06-09-product-roadmap.md)
> Research basis: [2026-06-13-feature-gap-analysis.md](2026-06-13-feature-gap-analysis.md) (candidate A;
> §2 reactive→proactive is the product's #1 category gap; §6 open questions)
> Builds on: M1 analyst + sandbox, slice 5 (actor/RBAC), slice 6 (grounding: `build_grounding`,
> authority badge, Sources).

## 1. Goal

Turn the agent from purely reactive into a background **monitor**. On a cadence (and on demand) it runs
cheap deterministic checks over business aggregates; anything that fires becomes a **grounded alert**
(cause narrative + Sources + authority badge from slice 6) in a shared, operator-gated **Alert Center**.
This closes the round-2 gap analysis's #1 finding (the product is entirely reactive) and is the slice's
demo headline: a console that lights up with *"Electronics sales −18% w/w — from `get_statistics`."*

## 2. Architecture

Five decisions (chosen over their alternatives during brainstorming):

1. **Deterministic detection, LLM only for cause.** Cheap threshold checks decide *what* fired (no LLM,
   controllable noise, testable); the LLM is invoked only to *explain* a finding that already fired.
2. **Detection evidence is the alert's primary grounding.** The deterministic reads that caused a check
   to fire are captured as `FindingEvidence` and persisted on the alert; the LLM cause pass may add
   supporting Sources, but it does not replace the provenance of the detection itself.
3. **Scoped monitor-cause pass.** Each new finding gets a cause narrative by reusing the eval-style
   pipeline (`agent.astream_events → capture → build_grounding`), **not** `run_turn` — there is no
   conversation thread for a background run. The cause agent is constrained: no sandbox, no charts,
   no filesystem, no `execute`, no `write_todos`; it may only use the small monitor read-tool set.
4. **In-process scheduler + manual trigger.** An asyncio interval loop in `lifespan` mirroring
   `_reap_loop`, off by default; plus `POST /api/monitor/run` for demos/tests. Both paths share a
   single `asyncio.Lock`; if a cycle is already running, a second manual/scheduled trigger returns
   "already running" instead of racing dedupe/create.
5. **System actor + shared operator Alert Center.** Cycles run as a service actor; alerts are a shared
   feed gated to the operator role (monitoring is about shared business state, not one user's sessions).

```
scheduler tick / POST /api/monitor/run
  → MonitorReader (system actor, global-aggregate MCP reads)
  → run checks → findings + FindingEvidence
  → dedupe vs open alerts (dedupe_key) and recently acknowledged alerts (cooldown)
  → for each NEW finding: monitor-cause pass → cause + optional cause grounding
  → merge detection evidence + cause Sources into alert grounding
  → persist Alert + publish AlertBus (SSE)
  → operator Alert Center renders badge + self-contained Sources
```

## 3. Components

**New (`src/ecommerce_agent/monitoring/`)**
- `checks.py` — deterministic checks. Each check: `name`, `run(reader) -> list[Finding]`. A `Finding` =
  `{check_name, severity, dedupe_key, title, metric, value, threshold, entities, evidence}`. `evidence` is
  a list of `FindingEvidence` records (`source_id`, `tool_name`, `args_summary`, `result_summary`,
  optional `evidence`) captured from the deterministic reader call that made the check fire. v1 registry:
  - `low_stock` — items below `monitor_low_stock_threshold` (from `inventory_low_stock`).
  - `sales_drop_wow` — category/total sales down ≥ `monitor_sales_drop_pct` vs the prior week (from
    `get_statistics` current vs prior period).
  Thresholds are config-driven; the registry is a list so checks are easy to add.
- `reader.py` — `MonitorReader` protocol (the few async aggregate fetches the checks need) + an MCP-backed
  impl bound to the system actor + an in-memory test double. Reader methods return structured values plus
  `FindingEvidence` so alert grounding can point at the actual detection read, not only a later cause read.
- `alerts.py` — `Alert` model + `AlertStore` protocol, `InMemoryAlertStore`, `MongoAlertStore`
  (`alerts` collection; `create`, `list(status=...)`, `get`, `acknowledge`; TTL `expire_at` reusing the
  slice-5 retention pattern, `alert_retention_days`). `Alert` stores both the human-facing `cause` and the
  merged `grounding`; its Sources always include the `FindingEvidence` that caused the deterministic check
  to fire. Alert Sources are self-contained summaries, not trace span links.
- `cause.py` — `explain_cause(agent, finding, settings) -> (cause_text, grounding_dict)`: builds a cause
  prompt from the finding and its detection evidence, runs the constrained monitor-cause agent through
  `capture()` into a `TraceRecord`, and returns `record.answer` + `build_grounding(record).to_dict()`.
  Mirrors the groundedness-eval runner, but uses monitor-specific instructions that forbid sandbox,
  charting, filesystem, `execute`, and `write_todos`.
- `runner.py` — `run_cycle(reader, checks, cause_agent, store, bus, settings) -> list[Alert]`: read →
  checks → dedupe → cause pass per new finding → merge detection evidence + cause grounding → persist +
  publish. Best-effort (see §7).
- `system.py` — builds the system `RuntimeActor` (service `spring_user_id` = `monitor_spring_user_id`) and
  a long-lived constrained monitor-cause agent using only monitor read tools (`get_statistics`,
  `inventory_low_stock`) and monitor-specific instructions. It is held by the monitor, **not** registered
  in `SessionRegistry` (so the reaper never evicts it).

**New (`src/ecommerce_agent/api/monitoring.py`)**
- `GET /api/alerts?status=` · `POST /api/alerts/{id}/acknowledge` · `POST /api/monitor/run` — operator-only
  via `require(Action.MANAGE_ALERTS)`. `GET /api/alerts/stream` — SSE.
- A minimal global `AlertBus` (broadcast pub/sub) for SSE, since `SessionBus` is per-session.

**Modified**
- `config.py` — `monitor_enabled` (default False), `monitor_interval_seconds` (default 900),
  `monitor_cooldown_seconds`, `monitor_low_stock_threshold`, `monitor_sales_drop_pct`,
  `monitor_spring_user_id`, `alert_retention_days`.
- `auth/models.py` + `auth/permissions.py` — add `Action.MANAGE_ALERTS` (operator only) to the central
  `can()` map.
- `api/app.py` — construct the alert store, monitor-cause agent, reader, run lock, and `AlertBus` in
  `lifespan`; launch `_monitor_loop` (gated by `monitor_enabled`, mirroring `_reap_loop`); mount the
  monitoring router; close resources on shutdown.
- Frontend — an **Alert Center** panel: alert list (severity, title, cause, slice-6 badge + Sources,
  acknowledge), live via the alerts SSE; operator-only. Reuse the badge styling/component, but render
  alert Sources inline from the alert payload instead of using slice 6's turn-trace fetch/jump path.

## 4. Alert grounding model

Alerts are not thread messages and do not own a persisted turn trace. Therefore their Sources are
self-contained:

- Detection Sources come from `FindingEvidence` and include `tool_name`, `args_summary`, `result_summary`,
  optional bounded `evidence`, and a synthetic `source_id`.
- Cause-pass Sources may be appended from the local `TraceRecord`, but their summaries/evidence are copied
  into the alert payload. The cause `TraceRecord` is not required to be persisted to `TraceStore`, and the
  Alert Center must not render "jump to trace" links for alert Sources.

Alert authority is also alert-specific. It is derived first from the deterministic detection evidence:

- `authoritative` when the check fired from canonical backend aggregate/read tools (`get_statistics`,
  `inventory_low_stock` in v1).
- `derived` only for future monitor checks whose detection depends on sandbox/computed evidence.
- `unverified` only if the alert lacks canonical detection evidence but contains numeric claims.

The monitor-cause pass can add supporting Sources and diagnostics, but it cannot downgrade a sound
deterministic alert to `unverified` merely because the cause agent did not call a data tool or failed.

## 5. Noise control & dedupe

Each `Finding` carries a stable `dedupe_key` (e.g. `low_stock:SKU-9`). Dedupe is two-tier:

1. If any **open** alert exists for the `dedupe_key`, skip the finding regardless of age — no duplicate
   open alert, no second LLM cause pass.
2. If the latest alert for the `dedupe_key` is acknowledged/closed, suppress repeats until
   `monitor_cooldown_seconds` has elapsed. After cooldown, a still-firing condition may create a new alert.

Auto-resolving an alert when its check stops firing is deferred. This + deterministic thresholds +
off-by-default scheduling are the noise/cost controls.

## 6. Scope (YAGNI)

**v1:** 2 checks; deterministic detection; detection evidence as primary Sources; **no monitor sandbox**;
constrained monitor-cause agent; scheduler off by default + manual run; shared operator Alert Center + SSE;
dedupe/cooldown; acknowledge.
**Deferred:** more / trend / ML-based checks; email or digest delivery; per-operator feeds; auto-resolve;
a monitor sandbox for deeper (sandbox-computed, `derived`) cause analysis.

## 7. Error handling

The cycle is best-effort and never crashes the loop (same posture as `_reap_loop`): a check or backend
failure is logged and that check skipped; the cycle continues. A cause-pass failure still emits the alert
from the deterministic finding, with a diagnostic and no narrative; the alert still carries the finding's
detection evidence as Sources. The scheduler loop wraps each cycle in try/except and sleeps to the next
tick regardless. Manual and scheduled triggers share the monitor run lock, so only one cycle executes at a
time.

## 8. RBAC & identity

Alerts and the manual run are operator-only via a new `Action.MANAGE_ALERTS` routed through `can()`
(viewers 403, unauth 401). The monitor's backend calls use the system actor's `monitor_spring_user_id`,
which is only ever used with **global-aggregate** tools (`get_statistics`, `inventory_low_stock`) — never
user-scoped queries.

## 9. Testing

- **Checks** (offline): stub `MonitorReader` → fired/not-fired, threshold boundaries, `entities`,
  `dedupe_key`, and `FindingEvidence`.
- **Dedupe** (offline): same finding with an open alert → skipped regardless of age; acknowledged alert
  within cooldown → skipped; acknowledged alert after cooldown → new alert.
- **Runner** (offline): fake reader + fake monitor-cause agent (scripted cause) + `InMemoryAlertStore` →
  end-to-end cycle yields grounded alerts with authoritative detection authority and inline detection
  Sources; a check that raises is skipped; a cause pass that raises → alert with diagnostic, no narrative,
  authoritative detection authority, and detection Sources.
- **Cause** (offline): a scripted monitor-cause agent record → `explain_cause` returns answer + grounding
  dict; construction excludes sandbox/chart/filesystem/execute/write_todos paths.
- **Alert store**: in-memory + Mongo (create/list-by-status/get/acknowledge; `expire_at` TTL).
- **API**: operator can list/ack/run; viewer 403; unauth 401; SSE emits a published alert.
- **Scheduler**: `_monitor_loop` calls `run_cycle` on tick (monkeypatched); respects `monitor_enabled`;
  manual + scheduled triggers share one lock and do not overlap.
- **Live (RUN_LIVE_LLM, optional)**: a seeded finding → real cause pass → grounded alert.
- **Frontend**: Alert Center renders alerts + badge/self-contained Sources; acknowledge; SSE update; alert
  Sources do not render trace-jump controls or call `/turns/{turn_id}/trace`.

## 10. Risks

- **Alert noise** (make-or-break): deterministic thresholds + dedupe + cooldown + off-by-default; tunable.
- **Cost/cadence:** LLM only on new findings; open-alert dedupe and cooldown after acknowledgement cap it;
  interval configurable.
- **Single-instance scheduler:** the in-process loop assumes one app instance (same assumption as the
  reaper). Fine now; flagged for any future multi-instance deploy.
- **System-actor scope:** restricted to global-aggregate tools by construction (`get_statistics`,
  `inventory_low_stock` only for v1); documented.
- **DeepAgents tool exclusion hook:** hiding filesystem/scaffolding tools for the cause pass currently uses
  DeepAgents' tool-exclusion middleware. Re-check this hook on DeepAgents upgrades because the public API is
  still profile-oriented.

## 11. Implementation checklist

> Folded-in plan (checklist, not a transcript — executed warm). TDD per item; commit per item.

- [ ] **Config + RBAC.** Add the `monitor_*` / `alert_retention_days` settings and `Action.MANAGE_ALERTS`
  to `can()` (operator only). Tests: config defaults; `can(viewer, MANAGE_ALERTS)` false, operator true.
- [ ] **Checks + Finding.** `monitoring/checks.py` with `Finding`, `FindingEvidence`, the check protocol,
  `low_stock`, `sales_drop_wow`, and a registry. Tests against a stub reader
  (fired/not/threshold/dedupe_key/evidence).
- [ ] **MonitorReader.** Protocol + MCP-backed impl (system actor) + in-memory double. Reader methods return
  structured aggregate data plus `FindingEvidence`. Test the double; the MCP impl is exercised via the
  runner/live.
- [ ] **Alert model + store.** `Alert`, `AlertStore` protocol, `InMemoryAlertStore`, `MongoAlertStore`
  (+ `expire_at` TTL). Tests: create/list-by-status/get/acknowledge (mem + Mongo).
- [ ] **Cause pass.** `monitoring/cause.py::explain_cause` reusing `capture` + `build_grounding` with a
  constrained monitor-cause agent. Test with a scripted event stream → answer + grounding, and verify the
  prompt forbids sandbox/chart/filesystem/execute/write_todos.
- [ ] **Runner + dedupe.** `monitoring/runner.py::run_cycle`. Tests: end-to-end with fakes; detection
  evidence appears in alert Sources; canonical detection evidence sets alert authority to `authoritative`;
  open-alert dedupe regardless of age; acknowledged-within-cooldown skipped; acknowledged-after-cooldown
  creates a new alert; check-raises-skipped; cause-raises-diagnostic without downgrading authority.
- [ ] **System cause agent.** `monitoring/system.py` — system `RuntimeActor` + constrained monitor-cause
  agent builder using only monitor read tools. Offline construction test (monkeypatched model/builder;
  no sandbox/chart/filesystem/execute/write_todos, not in `SessionRegistry`).
- [ ] **AlertBus + API.** Global broadcast bus; `api/monitoring.py` routes (`GET /api/alerts`,
  acknowledge, `POST /api/monitor/run`, `GET /api/alerts/stream`) operator-gated. Tests: operator/viewer/
  unauth; SSE emits a published alert.
- [ ] **Wire into app.** `lifespan`: build stores/cause-agent/reader/bus/run-lock; launch `_monitor_loop`
  (gated by `monitor_enabled`); mount router; close on shutdown. Test: loop calls `run_cycle` on tick;
  disabled → no loop; manual and scheduled triggers cannot overlap.
- [ ] **Frontend Alert Center.** Panel + SSE subscription + acknowledge, reusing slice-6 badge styling but
  rendering self-contained alert Sources inline (no trace fetch/jump controls); operator-only. Component
  tests.
- [ ] **Verification.** `uv run pytest -q`; `uv run ruff check src tests`; frontend lint + tests; optional
  `RUN_LIVE_LLM=1` cause-pass check.

## 12. Acceptance criteria

1. A monitoring cycle (manual `POST /api/monitor/run` or scheduled tick) runs the deterministic checks as
   the system actor and creates alerts only for findings that fire.
2. Each new finding produces a grounded alert (cause narrative + alert `grounding` with authority +
   self-contained Sources). Sources include the deterministic detection evidence; cause-pass tool Sources
   may be added inline. Canonical backend detection evidence sets authority to `authoritative`. A
   cause-pass failure still emits the alert with a diagnostic, authoritative detection authority, and
   detection Sources.
3. Dedupe prevents duplicate alerts (and duplicate LLM calls) for a still-open finding; cooldown only gates
   repeat alerts after a prior alert has been acknowledged/closed.
4. Alerts + manual run are operator-only (`MANAGE_ALERTS`); viewers 403, unauthenticated 401.
5. The Alert Center lists alerts with badge + inline Sources, supports acknowledge, updates live over SSE,
   and does not depend on turn trace fetches for alert evidence.
6. The scheduler loop is off by default, never crashes on a cycle error, is single-instance by design, and
   does not overlap with manual runs.
7. Default Python suite + scoped ruff pass; frontend tests pass.
