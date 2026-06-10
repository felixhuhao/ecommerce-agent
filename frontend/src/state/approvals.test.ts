import { describe, expect, it } from "vitest";
import type { ThreadMessage } from "../types";
import { foldApprovals } from "./approvals";

const m = (overrides: Partial<ThreadMessage>): ThreadMessage => ({
  message_id: "x",
  session_id: "s",
  seq: 0,
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

describe("foldApprovals", () => {
  it("folds proposal -> consumed with the execution result", () => {
    const views = foldApprovals([
      m({
        seq: 1,
        type: "agent_proposal",
        approval_id: "a1",
        tool_name: "purchase_order_create",
        status: "pending",
        card: { title: "PO" },
      }),
      m({ seq: 2, type: "approval_status", approval_id: "a1", status: "approved" }),
      m({
        seq: 3,
        type: "execution_result",
        approval_id: "a1",
        status: "consumed",
        result: { purchaseOrderId: 88 },
      }),
    ]);

    expect(views).toHaveLength(1);
    expect(views[0]).toMatchObject({
      approvalId: "a1",
      status: "consumed",
      toolName: "purchase_order_create",
      result: { purchaseOrderId: 88 },
      card: { title: "PO" },
    });
  });

  it("captures a rejected reason", () => {
    const views = foldApprovals([
      m({ seq: 1, type: "agent_proposal", approval_id: "a1", status: "pending" }),
      m({
        seq: 2,
        type: "approval_status",
        approval_id: "a1",
        status: "rejected",
        reason: "too costly",
      }),
    ]);
    expect(views[0]).toMatchObject({ status: "rejected", reason: "too costly" });
  });

  it("ignores messages without an approval_id", () => {
    expect(foldApprovals([m({ seq: 1, type: "agent_answer" })])).toEqual([]);
  });
});
