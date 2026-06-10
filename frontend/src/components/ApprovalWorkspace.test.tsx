import { render, screen, waitFor } from "@testing-library/react";
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
  it("labels purchase-order financial impact as estimated spend", () => {
    render(
      <ApprovalWorkspace
        {...base}
        approvals={[
          approval({
            card: {
              title: "Create purchase order",
              financialImpact: { totalCost: 90000 },
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Estimated Spend")).toBeInTheDocument();
    expect(screen.getByText("$90,000.00")).toBeInTheDocument();
  });

  it("does not repeat the raw tool slug when the card has a title", () => {
    render(
      <ApprovalWorkspace
        {...base}
        approvals={[
          approval({
            approvalId: "f96b5586-41a0-4d7b-9ead-1fbd55ea0dc9",
            card: {
              title: "Create purchase order",
              operationType: "create",
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Create purchase order")).toBeInTheDocument();
    expect(screen.queryByText("purchase_order_create")).not.toBeInTheDocument();
    expect(screen.getByText("f96b5586-41a0-4d7b-9ead-1fbd55ea0dc9")).toBeInTheDocument();
  });

  it("labels rejected approval reasons", () => {
    render(
      <ApprovalWorkspace
        {...base}
        approvals={[
          approval({
            status: "rejected",
            reason: "debug cleanup",
          }),
        ]}
      />,
    );

    expect(screen.getByText("Rejection reason")).toBeInTheDocument();
    expect(screen.getByText("debug cleanup")).toBeInTheDocument();
  });

  it("scrolls the matching card into view when focusApprovalId is set", () => {
    const scrollIntoView = vi.fn();
    const onFocusApprovalHandled = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(
      <ApprovalWorkspace
        {...base}
        approvals={[approval({ approvalId: "a2" }), approval({ approvalId: "a1" })]}
        focusApprovalId="a1"
        onFocusApprovalHandled={onFocusApprovalHandled}
      />,
    );

    expect(document.querySelector('[data-approval-id="a1"]')).not.toBeNull();
    return waitFor(() => {
      expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" });
      expect(onFocusApprovalHandled).toHaveBeenCalled();
    });
  });
});
