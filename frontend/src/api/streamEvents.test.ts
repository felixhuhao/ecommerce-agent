import { describe, expect, it } from "vitest";
import { parseStreamEvent } from "./streamEvents";

describe("parseStreamEvent", () => {
  it("parses a thread.append into a typed message event", () => {
    const ev = parseStreamEvent(
      "thread.append",
      JSON.stringify({
        event: "thread.append",
        message: { seq: 3, type: "agent_answer", content: "hi", turn_id: "t1" },
      }),
    );

    expect(ev).toEqual({
      kind: "thread.append",
      message: { seq: 3, type: "agent_answer", content: "hi", turn_id: "t1" },
    });
  });

  it("parses token/tool/done/error frames", () => {
    expect(parseStreamEvent("token", JSON.stringify({ text: "ab" }))).toEqual({
      kind: "token",
      text: "ab",
    });
    expect(parseStreamEvent("tool", JSON.stringify({ name: "order_query", phase: "start" }))).toEqual({
      kind: "tool",
      name: "order_query",
      phase: "start",
    });
    expect(
      parseStreamEvent(
        "turn.progress",
        JSON.stringify({
          turn_id: "t1",
          step_id: "tool:1",
          kind: "tool",
          label: "Reading data",
          status: "running",
          detail: "get_statistics",
          ts: 1.5,
        }),
      ),
    ).toEqual({
      kind: "turn.progress",
      step: {
        turnId: "t1",
        stepId: "tool:1",
        kind: "tool",
        label: "Reading data",
        status: "running",
        detail: "get_statistics",
        ts: 1.5,
      },
    });
    expect(parseStreamEvent("done", JSON.stringify({ turn_id: "t1" }))).toEqual({
      kind: "done",
      turnId: "t1",
    });
    expect(parseStreamEvent("error", JSON.stringify({ message: "boom" }))).toEqual({
      kind: "error",
      message: "boom",
    });
  });

  it("returns null for unknown or malformed frames", () => {
    expect(parseStreamEvent("nope", "{}")).toBeNull();
    expect(parseStreamEvent("token", "not json")).toBeNull();
    expect(parseStreamEvent("tool", JSON.stringify({ name: 123 }))).toBeNull();
    expect(parseStreamEvent("turn.progress", JSON.stringify({ status: "nope" }))).toBeNull();
    expect(parseStreamEvent("done", JSON.stringify({}))).toBeNull();
  });
});
