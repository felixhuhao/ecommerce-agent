import { describe, expect, it, vi } from "vitest";
import { performSend } from "./sendMessage";

describe("performSend", () => {
  it("marks the turn started on 202 and appends no optimistic message", async () => {
    const api = { postMessage: vi.fn(async () => ({ turnInProgress: false as const, turnId: "t1" })) };
    const markTurnStarted = vi.fn();

    const result = await performSend("s1", "hi", api, markTurnStarted);

    expect(markTurnStarted).toHaveBeenCalledWith("t1");
    expect(result).toEqual({ busy: false });
  });

  it("reports busy on 409 and does not start a turn", async () => {
    const api = { postMessage: vi.fn(async () => ({ turnInProgress: true as const })) };
    const markTurnStarted = vi.fn();

    const result = await performSend("s1", "hi", api, markTurnStarted);

    expect(markTurnStarted).not.toHaveBeenCalled();
    expect(result).toEqual({ busy: true });
  });
});
