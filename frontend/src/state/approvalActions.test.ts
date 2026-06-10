import { describe, expect, it, vi } from "vitest";
import { performApprove } from "./approvalActions";

describe("performApprove", () => {
  it("refetches the thread on a 409 conflict (server-driven card)", async () => {
    const api = { approveApproval: vi.fn(async () => ({ conflict: true as const, body: {} })) };
    const onConflict = vi.fn();

    await performApprove("s1", "a1", api, onConflict);

    expect(onConflict).toHaveBeenCalledOnce();
  });

  it("does not refetch on success (durable messages arrive via the stream)", async () => {
    const api = { approveApproval: vi.fn(async () => ({ conflict: false as const, body: {} })) };
    const onConflict = vi.fn();

    await performApprove("s1", "a1", api, onConflict);

    expect(onConflict).not.toHaveBeenCalled();
  });
});
