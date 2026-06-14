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

Four decisions (chosen over their alternatives during brainstorming):

1. **Deterministic detection, LLM only for cause.** Cheap threshold checks decide *what* fired (no LLM,
   controllable noise, testable); the LLM is invoked only to *explain* a finding that already fired.
2. **Scoped grounded analyst cause pass.** Each new finding gets a cause narrative + grounding by reusing
   the eval-style pipeline (`agent.astream_events → capture → build_grounding`), **not** `run_turn` —
   there is no conversation thread for a background run.
3. **In-process scheduler + manual trigger.** An asyncio interval loop in `lifespan` mirroring
   `_reap_loop`, off by default; plus `POST /api/monitor/run` for demos/tests.
4. **System actor + shared operator Alert Center.** Cycles run as a service actor; alerts are a shared
   feed gated to the operator role (monitoring is about shared business state, not one user's sessions).

```
scheduler tick / POST /api/monitor/run
  → MonitorReader (system actor, global-aggregate MCP reads)
  → run checks → findings
  → dedupe vs open alerts (dedupe_key + cooldown)
  → for each NEW finding: grounded analyst cause pass → cause + grounding
  → persist Alert + publish AlertBus (SSE)
  → operator Alert Center renders (badge + Sources from slice 6)
```

## 3. Components

**New (`src/ecommerce_agent/monitoring/`)**
- `checks.py` — deterministic checks. Each check: `name`, `run(reader) -> list[Finding]`. A `Finding` =
  `{check_name, severity, dedupe_key, title, metric, value, threshold, entities}`. v1 registry:
  - `low_stock` — items below `monitor_low_stock_threshold` (from `inventory_low_stock`).
  - `sales_drop_wow` — category/total sales down ≥ `monitor_sales_drop_pct` vs the prior week (from
    `get_statistics` current vs prior period).
  Thresholds are config-driven; the registry is a list so checks are easy to add.
- `reader.py` — `MonitorReader` protocol (the few async aggregate fetches the checks need) + an MCP-backed
  impl bound to the system actor + an in-memory test double.
- `alerts.py` — `Alert` model + `AlertStore` protocol, `InMemoryAlertStore`, `MongoAlertStore`
  (`alerts` collection; `create`, `list(status=...)`, `get`, `acknowledge`; TTL `expire_at` reusing the
  slice-5 retention pattern, `alert_retention_days`).
- `cause.py` — `explain_cause(analyst, finding, settings) -> (cause_text, grounding_dict)`: builds a cause
  prompt from the finding, runs the analyst through `capture()` into a `TraceRecord`, returns
  `record.answer` + `build_grounding(record).to_dict()`. Mirrors the groundedness-eval runner.
- `runner.py` — `run_cycle(reader, checks, analyst, store, bus, settings) -> list[Alert]`: read → checks
  → dedupe → cause pass per new finding → persist + publish. Best-effort (see §6).
- `system.py` — builds the system `RuntimeActor` (service `spring_user_id` = `monitor_spring_user_id`) and
  a long-lived sandbox-less analyst (`build_sales_analyst(..., backend=None, viz_tools=[])`), held by the
  monitor, **not** registered in `SessionRegistry` (so the reaper never evicts it).

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
- `api/app.py` — construct the alert store, monitor analyst, reader, and `AlertBus` in `lifespan`; launch
  `_monitor_loop` (gated by `monitor_enabled`, mirroring `_reap_loop`); mount the monitoring router; close
  resources on shutdown.
- Frontend — an **Alert Center** panel: alert list (severity, title, cause, slice-6 badge + Sources,
  acknowledge), live via the alerts SSE; operator-only.

## 4. Noise control & dedupe

Each `Finding` carries a stable `dedupe_key` (e.g. `low_stock:SKU-9`). A finding whose `dedupe_key` already
has an **open** alert within `monitor_cooldown_seconds` is skipped — no duplicate alert, no second LLM
cause pass. (Auto-resolving an alert when its check stops firing is deferred.) This + deterministic
thresholds + off-by-default scheduling are the noise/cost controls.

## 5. Scope (YAGNI)

**v1:** 2 checks; deterministic detection; **no monitor sandbox**; scheduler off by default + manual run;
shared operator Alert Center + SSE; dedupe/cooldown; acknowledge.
**Deferred:** more / trend / ML-based checks; email or digest delivery; per-operator feeds; auto-resolve;
a monitor sandbox for deeper (sandbox-computed, `derived`) cause analysis.

## 6. Error handling

The cycle is best-effort and never crashes the loop (same posture as `_reap_loop`): a check or backend
failure is logged and that check skipped; the cycle continues. A cause-pass failure still emits the alert
from the deterministic finding, with a diagnostic and no narrative. The scheduler loop wraps each cycle in
try/except and sleeps to the next tick regardless.

## 7. RBAC & identity

Alerts and the manual run are operator-only via a new `Action.MANAGE_ALERTS` routed through `can()`
(viewers 403, unauth 401). The monitor's backend calls use the system actor's `monitor_spring_user_id`,
which is only ever used with **global-aggregate** tools (`get_statistics`, `inventory_low_stock`) — never
user-scoped queries.

## 8. Testing

- **Checks** (offline): stub `MonitorReader` → fired/not-fired, threshold boundaries, `entities`/`dedupe_key`.
- **Dedupe** (offline): same finding twice within cooldown → one alert; after cooldown → a new alert.
- **Runner** (offline): fake reader + fake analyst (scripted cause) + `InMemoryAlertStore` → end-to-end
  cycle yields grounded alerts; a check that raises is skipped; a cause pass that raises → alert with
  diagnostic, no narrative.
- **Cause** (offline): a scripted analyst record → `explain_cause` returns answer + grounding dict.
- **Alert store**: in-memory + Mongo (create/list-by-status/get/acknowledge; `expire_at` TTL).
- **API**: operator can list/ack/run; viewer 403; unauth 401; SSE emits a published alert.
- **Scheduler**: `_monitor_loop` calls `run_cycle` on tick (monkeypatched); respects `monitor_enabled`.
- **Live (RUN_LIVE_LLM, optional)**: a seeded finding → real cause pass → grounded alert.
- **Frontend**: Alert Center renders alerts + badge/Sources; acknowledge; SSE update.

## 9. Risks

- **Alert noise** (make-or-break): deterministic thresholds + dedupe + cooldown + off-by-default; tunable.
- **Cost/cadence:** LLM only on new findings; cooldown caps it; interval configurable.
- **Single-instance scheduler:** the in-process loop assumes one app instance (same assumption as the
  reaper). Fine now; flagged for any future multi-instance deploy.
- **System-actor scope:** restricted to global-aggregate tools by construction; documented.

## 10. Implementation checklist

> Folded-in plan (checklist, not a transcript — executed warm). TDD per item; commit per item.

- [ ] **Config + RBAC.** Add the `monitor_*` / `alert_retention_days` settings and `Action.MANAGE_ALERTS`
  to `can()` (operator only). Tests: config defaults; `can(viewer, MANAGE_ALERTS)` false, operator true.
- [ ] **Checks + Finding.** `monitoring/checks.py` with `Finding`, the check protocol, `low_stock`,
  `sales_drop_wow`, and a registry. Tests against a stub reader (fired/not/threshold/dedupe_key).
- [ ] **MonitorReader.** Protocol + MCP-backed impl (system actor) + in-memory double. Test the double;
  the MCP impl is exercised via the runner/live.
- [ ] **Alert model + store.** `Alert`, `AlertStore` protocol, `InMemoryAlertStore`, `MongoAlertStore`
  (+ `expire_at` TTL). Tests: create/list-by-status/get/acknowledge (mem + Mongo).
- [ ] **Cause pass.** `monitoring/cause.py::explain_cause` reusing `capture` + `build_grounding`. Test with
  a scripted analyst event stream → answer + grounding.
- [ ] **Runner + dedupe.** `monitoring/runner.py::run_cycle`. Tests: end-to-end with fakes; dedupe/cooldown;
  check-raises-skipped; cause-raises-diagnostic.
- [ ] **System analyst.** `monitoring/system.py` — system `RuntimeActor` + sandbox-less analyst builder.
  Offline construction test (monkeypatched model/builder; `backend=None`, not in `SessionRegistry`).
- [ ] **AlertBus + API.** Global broadcast bus; `api/monitoring.py` routes (`GET /api/alerts`,
  acknowledge, `POST /api/monitor/run`, `GET /api/alerts/stream`) operator-gated. Tests: operator/viewer/
  unauth; SSE emits a published alert.
- [ ] **Wire into app.** `lifespan`: build stores/analyst/reader/bus; launch `_monitor_loop` (gated by
  `monitor_enabled`); mount router; close on shutdown. Test: loop calls `run_cycle` on tick; disabled →
  no loop.
- [ ] **Frontend Alert Center.** Panel + SSE subscription + acknowledge, reusing slice-6 badge/Sources;
  operator-only. Component tests.
- [ ] **Verification.** `uv run pytest -q`; `uv run ruff check src tests`; frontend lint + tests; optional
  `RUN_LIVE_LLM=1` cause-pass check.

## 11. Acceptance criteria

1. A monitoring cycle (manual `POST /api/monitor/run` or scheduled tick) runs the deterministic checks as
   the system actor and creates alerts only for findings that fire.
2. Each new finding produces a grounded alert (cause narrative + slice-6 `grounding` with authority +
   Sources); a cause-pass failure still emits the alert with a diagnostic.
3. Dedupe/cooldown prevents duplicate alerts (and duplicate LLM calls) for a still-open finding.
4. Alerts + manual run are operator-only (`MANAGE_ALERTS`); viewers 403, unauthenticated 401.
5. The Alert Center lists alerts with badge + Sources, supports acknowledge, and updates live over SSE.
6. The scheduler loop is off by default, never crashes on a cycle error, and is single-instance by design.
7. Default Python suite + scoped ruff pass; frontend tests pass.
