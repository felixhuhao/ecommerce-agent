# M4 Slice 8 — Live Turn Status Design

## 1. Goal

Replace per-message `Inspect` as the primary operator affordance with a real-time turn status tracker.

The `Trace` tab remains a post-chat analysis/debug surface. The live tracker is not a mini trace viewer; it is a compact operational view that answers: "What is the agent doing right now?"

## 2. UX Shape

During an in-flight turn, show a compact status block at the bottom of the conversation, near the provisional answer/composer:

```text
Working
✓ Routing request
✓ Sales analyst selected
• Reading sales data
  Generating chart
  Preparing answer
```

Behavior:

- Status block appears only while a turn is running.
- The latest active step is visually emphasized.
- Completed steps remain visible for context.
- On success, the block collapses/disappears once the durable answer/proposal arrives.
- On failure, keep a short failed state next to the existing error notice.
- Remove `Inspect` buttons from chat messages.
- Remove trace jump links from `SourcesExpander`; sources remain expandable in place.
- Keep the `Trace` tab for manual post-chat analysis.

The header `activeTool` chip can be removed or demoted after this lands; the tracker supersedes it.

## 3. Trace Tab Role

Trace is still valuable, but it should feel like analysis, not the normal path.

Keep:

- span list
- args/result/evidence
- timings/tokens
- approval/artifact ids
- JSON export

Change:

- No per-message `Inspect` entry point.
- Trace tab must choose what to show without an empty default:
  - default to the latest completed agent/proposal turn with a `turn_id`, and
  - provide a small completed-turn selector when multiple traced turns exist.

This is part of the slice. Removing `Inspect` without a Trace-owned turn selector would orphan the
Trace tab and regress debuggability.

## 4. Event Model

Add a new SSE event: `turn.progress`.

Suggested payload:

```json
{
  "event": "turn.progress",
  "turn_id": "turn-123",
  "step_id": "tool:get_statistics:run-id",
  "kind": "tool",
  "label": "Reading sales data",
  "status": "running",
  "detail": "get_statistics",
  "ts": 1781430000.123
}
```

Fields:

- `turn_id`: required; ignore events for other turns.
- `step_id`: stable per step; used to update start/end.
- `kind`: `routing | specialist | tool | approval | artifact | answer | error`.
- `label`: operator-facing text.
- `status`: `pending | running | done | failed`.
- `detail`: optional internal name such as tool/specialist.
- `ts`: optional ordering timestamp.

Keep existing `tool` frames for compatibility initially, but new UI should use `turn.progress`.

## 5. Backend Sources

Publish progress from the same places that already produce stream/trace signals:

- Routing request: optional synthetic `Routing request` emitted manually before the agent event loop.
- Route decision: `<Specialist> selected`.
- Tool start/end from `TraceEvent(event_type="tool_call")`.
- `request_approval` end: `Approval requested`.
- chart artifact end: `Chart generated`.
- before appending final answer/proposal: synthetic `Preparing answer` emitted manually before
  `_append_turn_result`.
- exception path: failed progress event + existing error frame.

Only tool start/end, route decision, approval request, and chart artifact events come directly from
captured trace events. `Routing request` and `Preparing answer` do not exist in `capture()` today;
`run_turn` must inject them.

`Starting turn` is frontend-owned. The `turn_started` local reducer action seeds it immediately on
send, so the backend must not also emit a `turn.progress` row for that step.

Routing currently has no "start" event. A route decision appears only when the decision is known, so
`<Specialist> selected` should be emitted directly as `done`, not as a running step.

Tool labels should be user-facing and deterministic:

- `get_statistics` -> `Reading business data`
- `inventory_low_stock` -> `Reading inventory data`
- `stage_sales_analysis_inputs` -> `Staging analysis inputs`
- `execute` -> `Running analysis`
- chart tools -> `Generating chart`
- `request_approval` -> `Requesting approval`
- unknown tools -> `Using <tool_name>`

Do not expose raw args, evidence, tokens, or model call internals in live status.

## 6. Frontend State

Extend `SessionState` with progress for the current turn:

```ts
interface TurnProgressStep {
  stepId: string;
  kind: string;
  label: string;
  status: "pending" | "running" | "done" | "failed";
  detail: string | null;
  ts: number | null;
}
```

Reducer rules:

- `turn_started`: clear progress and seed `Starting turn`.
- `turn.progress`: upsert by `stepId`, preserve order by first seen / `ts`.
- `thread.append` terminal answer/proposal: finalize and clear/hide progress.
- `done`: finalize and clear/hide progress.
- `error`: mark the current running step failed and keep progress visible with the error notice.

This requires changing the existing `error` reducer behavior. Today `error` calls `finalize()`, which
clears transient turn state. With progress, the reducer needs to keep enough state to mark the active
step failed instead of wiping the tracker immediately.

Render with a new `TurnStatusTracker` component inside `ConversationView`.

Anchor the tracker to `inFlightTurnId`, not to provisional answer text. The backend does not currently
publish token frames for normal answers, so the tracker must work even when `tokenBuffer` is empty.

## 7. Scope

In scope:

- new SSE event parsing
- session reducer progress state
- compact conversation-level status tracker
- remove chat `Inspect` buttons
- remove `SourcesExpander` trace jump links
- Trace tab latest-turn default + completed-turn selector
- keep Trace tab available

Out of scope:

- full Trace tab redesign
- persisted progress history
- live args/evidence display
- multi-turn timeline analytics
- operator-configurable verbosity

## 8. Tests

Backend:

- `run_turn` publishes `turn.progress` for tool start/end.
- approval request emits `Approval requested`.
- artifact-producing tool emits `Chart generated`.
- failure path emits failed progress and existing `error`.

Frontend:

- `parseStreamEvent` parses `turn.progress`.
- reducer upserts progress steps and clears them on terminal answer/done.
- reducer marks the active running step failed on `error` without immediately wiping progress.
- `ConversationView` renders tracker while in flight.
- `Inspect` button no longer renders on agent/proposal messages.
- `SourcesExpander` no longer exposes trace jump buttons.
- Trace tab defaults to the latest completed traced turn and can select older traced turns.
- Trace tab still fetches and renders when selected.

## 9. Open Questions

1. Should completed status auto-hide immediately when the answer arrives, or linger for ~2 seconds?
   - Default: hide immediately; the durable answer is enough.
2. Should Trace default to latest completed turn in this slice?
   - Resolved: yes. Minimal latest-turn default + selector is required before removing `Inspect`.
3. Should model calls appear in live status?
   - Default: no. Show operator-facing phases, not model internals.

## 10. Acceptance

- During a normal turn, the user sees a readable sequence of live activity.
- Raw `Inspect` buttons are gone from chat messages.
- Sources no longer contain trace jump links.
- Trace tab still has a working turn entry point without message-level Inspect.
- Trace remains accessible as post-chat analysis.
- Existing durable answer/thread behavior is unchanged.
- No raw trace details leak into the live status tracker.

## 11. Follow-Up Cleanup

During migration, `tool` frames may continue to feed the old `activeTool` field. After the tracker is
stable, remove or stop consuming `tool` frames and delete the header active-tool chip so tool activity
has one UI representation.
