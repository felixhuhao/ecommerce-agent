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
  });

  it("finalizes on a done frame for the in-flight turn", () => {
    let state = initialSessionState();
    state = sessionReducer(state, { kind: "turn_started", turnId: "t1" });
    state = sessionReducer(state, { kind: "done", turnId: "t1" });
    expect(state.inFlightTurnId).toBeNull();
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
    state = sessionReducer(state, { kind: "tool", name: "search", phase: "start" });
    state = sessionReducer(state, { kind: "error", message: "something broke" });
    expect(state.inFlightTurnId).toBeNull();
    expect(state.tokenBuffer).toBe("");
    expect(state.activeTool).toBeNull();
    expect(state.error).toBe("something broke");
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
