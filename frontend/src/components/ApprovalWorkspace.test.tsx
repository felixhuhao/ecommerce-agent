import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalWorkspace } from "./ApprovalWorkspace";
import type { ApprovalView } from "../state/approvals";

function approval(overrides: Partial<ApprovalView> = {}): ApprovalView {
  return {
    approvalId: "a1",
    card: null,
    toolName: "purchase_order_create",
    status: "pending",
    result: null,
    reason: null,
    ...overrides,
  };
}

const base = {
  pendingApprovalId: null,
  actionError: null,
  onApprove: vi.fn(),
  onReject: vi.fn(),
};

describe("ApprovalWorkspace focus", () => {
  it("scrolls the matching card into view when focusApprovalId is set", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(
      <ApprovalWorkspace
        {...base}
        approvals={[approval({ approvalId: "a2" }), approval({ approvalId: "a1" })]}
        focusApprovalId="a1"
      />,
    );

    expect(document.querySelector('[data-approval-id="a1"]')).not.toBeNull();
    expect(scrollIntoView).toHaveBeenCalled();
  });
});
