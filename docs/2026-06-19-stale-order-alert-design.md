# Stale Order Alert Design

## 1. Goal

Add a deterministic proactive alert for orders that appear operationally stuck.

This is the next alert type after low stock because it is concrete, operational,
and easy to verify:

- pending orders that have not progressed for too long;
- paid orders that have not shipped for too long.

The alert should help operators notice order-flow problems. It should not create
or execute proposals automatically.

## 2. Decision

Add a `stale_order` monitor check.

V1 rules:

| Status | Alert When | Severity |
|---|---|---|
| `pending` | age >= `monitor_stale_pending_order_hours` | warning |
| `paid` | age >= `monitor_stale_paid_order_hours` | warning |

Default thresholds should be demo-friendly but not noisy:

- pending: 48 hours;
- paid: 24 hours.

The check is deterministic. It does not require the monitor-cause LLM pass to be
useful.

## 3. Current Architecture Fit

The existing monitor stack already supports this shape:

- `MonitorCheck.run(reader) -> list[Finding]`;
- `FindingEvidence` is persisted as self-contained alert Sources;
- `run_monitor_cycle` handles dedupe, cooldown, store, and SSE;
- Alert Center renders alert title, values, grounding, Sources, and acknowledge.

The new work should add status-specific reader methods and one check, not a new
alert system.

## 4. Data Contract

Preferred source: Spring MCP `order_query`.

The monitor reader should expose separate status-specific methods:

```text
stale_pending_order_candidates() -> order_query(status="pending", ...)
stale_paid_order_candidates()    -> order_query(status="paid", ...)
```

Each row must provide enough fields to calculate and explain staleness:

- `orderId` or `order_id`;
- `userId` or `user_id`;
- `status`;
- `createdAt` / `created_at`;
- `paidAt` / `paid_at` for paid orders;
- `totalAmount` / `amount` if available.

Spring timestamps are local date-times without offsets. The Spring stale filter
is the authority for whether a row is stale; Python should treat naive
timestamps as local wall time when computing display age so it does not compare
Spring-local rows against a second UTC clock.

Age source is status-aware:

| Status | Age Anchor | Reason |
|---|---|---|
| `pending` | `createdAt` | pending means the order has not reached payment yet |
| `paid` | `paidAt` | paid staleness means paid-but-not-shipped time, not order age |

If a row lacks the required anchor for its status, skip the row and include no
finding. The check should log skipped rows so malformed
paid-without-`paidAt` data is observable during tests and manual runs.

### 4.1 Oldest-Row Visibility

`order_query` currently returns newest orders first and caps results. That is a
blocker for a stale-order monitor because the oldest rows are the rows most
likely to be stale.

The implementation must resolve this before completing the slice. Preferred
options:

1. Extend Spring `order_query` with a status-aware age filter suitable for
   monitoring, for example `staleOlderThanHours` plus a status-specific anchor
   (`createdAt` for pending, `paidAt` for paid).
2. Add a dedicated Spring MCP read tool such as `stale_order_query`.
3. Add server-side status-specific stale-order aggregate endpoints.

Do not rely on `order_query(status=...)` returning the newest 50 rows. That can
pass small demo data while missing real stale orders.

Whichever contract is chosen, its actual tool name must be used consistently in:

- `FindingEvidence.tool_name`;
- the alert canonical detection set in `monitoring/grounding.py`;
- grounding and reader tests.

If option 2 or 3 uses a new tool name, do not leave the design's `order_query`
examples unchanged in code. A mismatched tool name silently downgrades alert
authority to `unverified`.

## 5. Finding Shape

For a stale pending order:

```text
check_name: stale_order
dedupe_key: stale_order:pending:<order_id>
title: Stale pending order: <order_id>
metric: stale_order_age_hours
value: <age_hours>
threshold: <pending_threshold_hours>
entities:
  orderId
  userId
  status
  ageHours
  createdAt
  totalAmount
evidence:
  tool_name: order_query
  args_summary: status=pending
```

For a stale paid order:

```text
dedupe_key: stale_order:paid:<order_id>
title: Paid order not shipped: <order_id>
threshold: <paid_threshold_hours>
entities:
  orderId
  userId
  status
  ageHours
  paidAt
  totalAmount
evidence:
  tool_name: order_query
  args_summary: status=paid
```

Dedupe should be status-specific. If an order moves from `pending` to `paid` and
then becomes stale again, that is a different operational condition and should
be allowed to alert.

## 6. Grounding

Add `order_query` to the alert canonical detection set.

Today alert authority is name-based in `monitoring/grounding.py`:

```python
CANONICAL_DETECTION_TOOLS = {"get_statistics", "inventory_low_stock"}
```

For stale orders, `order_query` is a canonical Spring operational read, so a
finding detected from `order_query` should render as `authoritative`.

## 7. Configuration

Add settings:

```text
monitor_stale_pending_order_hours = 48
monitor_stale_paid_order_hours = 24
```

Keep them separate from `monitor_cooldown_seconds`:

- stale thresholds are sent to the Spring stale-order query;
- cooldown decides when an acknowledged condition can alert again.

## 8. UX

Alert card should be self-contained:

- title: `Stale pending order: 1008` or `Paid order not shipped: 1012`;
- status badge: warning;
- values: age hours/days, threshold, order status, amount if available;
- Sources: inline `order_query` evidence;
- action: acknowledge.

No automatic approval proposal in v1.

Operators can manually ask the order manager:

```text
cancel pending order 1008
ship paid order 1012
```

## 9. Tests

Unit tests:

- returned pending stale candidates produce findings;
- returned paid stale candidates produce findings;
- Python does not re-threshold server-filtered candidates with a second clock;
- rows without timestamps are skipped;
- pending age uses `createdAt`;
- paid age uses `paidAt`, not `createdAt`;
- dedupe key includes status and order ID;
- evidence uses `order_query`;
- alert grounding marks `order_query` detections authoritative;
- check uses an injectable `now_fn` so display-age tests are deterministic.

Reader tests:

- `InMemoryMonitorReader.stale_pending_order_candidates(...)` returns rows and
  pending evidence;
- `InMemoryMonitorReader.stale_paid_order_candidates(...)` returns rows and paid
  evidence;
- MCP reader invokes the chosen stale-order Spring contract for each monitored
  status.

Runner/API tests:

- existing dedupe/cooldown tests cover the new finding shape;
- manual monitor run can create stale-order alerts with the fake reader.

Frontend tests:

- Alert Center renders stale-order entities and Sources;
- acknowledge still works.

## 10. Smoke / Manual Test

Seed or identify one old pending order and one old paid order.

Manual flow:

1. Run `POST /api/monitor/run`.
2. Confirm a stale order alert appears.
3. Confirm authority is `Authoritative`.
4. Expand Sources and verify `order_query` evidence.
5. Acknowledge the alert.
6. Re-run monitor within cooldown and confirm no duplicate open alert.

## 11. Non-Goals

- No auto-cancel or auto-ship proposal.
- No LLM judgment about whether the order "should" be cancelled.
- No customer notification workflow.
- No SLA calendar/business-hours logic.
- No warehouse/NL2SQL dependency.
- No shipped/completed/cancelled aging rules in v1. The status-aware age-source
  model can be extended later if those states become useful.

## 12. Acceptance

- Low-stock alerts continue to work.
- Stale pending/paid orders create deterministic alerts.
- Alert authority is authoritative when backed by `order_query`.
- No duplicate alerts for the same open stale condition.
- Viewer users still cannot access Alert Center.
- Manual smoke can produce and acknowledge a stale-order alert.
