import { describe, expect, it } from "vitest";
import type { ThreadMessage } from "../types";
import { initialSessionState, sessionReducer } from "./sessionReducer";

const msg = (overrides: Partial<ThreadMessage>): ThreadMessage => ({
  message_id: "m",
  session_id: "s1",
  seq: 1,
  type: "user",
  content: "",
  created_at: "",
  turn_id: null,
  trace_id: null,
  actor_id: null,
  execution_id: null,
  approval_id: null,
  card: null,
  tool_name: null,
  status: null,
  result: null,
  grounding: null,
  reason: null,
  ...overrides,
});

describe("sessionReducer", () => {
  it("upserts messages by seq (idempotent dedupe)", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "thread.append", message: msg({ seq: 1, content: "a" }) });
    state = sessionReducer(state, { kind: "thread.append", message: msg({ seq: 1, content: "a" }) });
    state = sessionReducer(state, {
      kind: "thread.append",
      message: msg({ seq: 2, type: "agent_answer", content: "b" }),
    });
    expect(state.messages.map((message) => message.seq)).toEqual([1, 2]);
  });

  it("accumulates tokens into a provisional bubble for the in-flight turn", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "token", text: "Hel" });
    state = sessionReducer(state, { kind: "token", text: "lo" });
    expect(state.inFlightTurnId).toBe("t1");
    expect(state.tokenBuffer).toBe("Hello");
    expect(state.turnProgress[0]).toMatchObject({
      stepId: "start:t1",
      label: "Starting turn",
      status: "running",
    });
  });

  it("finalizes the turn when the durable agent_answer arrives (reconnect-safe, no done frame)", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "token", text: "Hi" });
    state = sessionReducer(state, {
      kind: "thread.append",
      message: msg({ seq: 5, type: "agent_answer", content: "Hi", turn_id: "t1" }),
    });
    expect(state.inFlightTurnId).toBeNull();
    expect(state.tokenBuffer).toBe("");
    expect(state.turnProgress).toEqual([]);
  });

  it("finalizes on a done frame for the in-flight turn", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "done", turnId: "t1" });
    expect(state.inFlightTurnId).toBeNull();
    expect(state.turnProgress).toEqual([]);
  });

  it("upserts turn progress and completes the starting seed", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, {
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:stats",
        kind: "tool",
        label: "Reading sales data",
        status: "running",
        detail: "get_statistics",
        ts: 1,
      },
    });
    expect(state.turnProgress.map((step) => step.status)).toEqual(["done", "running"]);

    state = sessionReducer(state, {
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:stats",
        kind: "tool",
        label: "Reading sales data",
        status: "done",
        detail: "get_statistics",
        ts: 2,
      },
    });

    expect(state.turnProgress).toHaveLength(2);
    expect(state.turnProgress[1]).toMatchObject({ stepId: "tool:stats", status: "done", ts: 2 });
  });

  it("does not mark concurrent running tools done when another tool starts", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, {
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:a",
        kind: "tool",
        label: "Using tool A",
        status: "running",
        detail: "a",
        ts: 1,
      },
    });
    state = sessionReducer(state, {
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:b",
        kind: "tool",
        label: "Using tool B",
        status: "running",
        detail: "b",
        ts: 2,
      },
    });

    expect(state.turnProgress.find((step) => step.stepId === "tool:a")).toMatchObject({
      status: "running",
    });
    expect(state.turnProgress.find((step) => step.stepId === "tool:b")).toMatchObject({
      status: "running",
    });
  });

  it("tracks the active tool and clears it on turn end", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "tool", name: "order_query", phase: "start" });
    expect(state.activeTool).toBe("order_query");
    state = sessionReducer(state, { kind: "done", turnId: "t1" });
    expect(state.activeTool).toBeNull();
  });

  it("reset clears all state (session switch)", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "thread.append", message: msg({ seq: 1, content: "a" }) });
    state = sessionReducer(state, { kind: "reset" });
    expect(state.messages).toEqual([]);
    expect(state.inFlightTurnId).toBeNull();
    expect(state.tokenBuffer).toBe("");
  });

  it("finalizes the in-flight turn on error", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "token", text: "partial" });
    state = sessionReducer(state, {
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:search",
        kind: "tool",
        label: "Using search",
        status: "running",
        detail: "search",
        ts: 1,
      },
    });
    state = sessionReducer(state, { kind: "error", message: "something broke" });
    expect(state.inFlightTurnId).toBeNull();
    expect(state.tokenBuffer).toBe("");
    expect(state.activeTool).toBeNull();
    expect(state.error).toBe("something broke");
    expect(state.turnProgress.at(-1)).toMatchObject({ stepId: "tool:search", status: "failed" });

    state = sessionReducer(state, {
      kind: "thread.append",
      message: msg({
        seq: 5,
        type: "agent_answer",
        content: "failed",
        turn_id: "t1",
        status: "failed",
      }),
    });
    expect(state.turnProgress.at(-1)).toMatchObject({ stepId: "tool:search", status: "failed" });
  });

  it("thread_loaded rebuilds state from the authoritative thread (409 reconcile)", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, {
      kind: "thread_loaded",
      messages: [
        msg({ seq: 1, type: "user", content: "a" }),
        msg({ seq: 2, type: "approval_status", approval_id: "x", status: "rejected" }),
      ],
    });
    expect(state.messages.map((message) => message.seq)).toEqual([1, 2]);
    expect(state.inFlightTurnId).toBeNull();
  });
});
