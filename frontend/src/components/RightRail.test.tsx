import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";

const nodes = {
  alerts: <div>ALERTS</div>,
  approvals: <div>APPROVALS</div>,
  trace: <div>TRACE</div>,
  health: <div>HEALTH</div>,
};

describe("RightRail", () => {
  it("renders only the active tab's panel and a pending badge", () => {
    render(
      <RightRail
        activeTab="approvals"
        onTabChange={vi.fn()}
        approvalCount={2}
        alertCount={1}
        {...nodes}
      />,
    );
    expect(screen.getByText("APPROVALS")).toBeInTheDocument();
    expect(screen.queryByText("TRACE")).not.toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("switches tab on click", () => {
    const onTabChange = vi.fn();
    render(
      <RightRail
        activeTab="approvals"
        onTabChange={onTabChange}
        approvalCount={0}
        alertCount={0}
        {...nodes}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Trace" }));
    expect(onTabChange).toHaveBeenCalledWith("trace");
    expect(screen.queryByRole("tab", { name: "Artifacts" })).not.toBeInTheDocument();
  });

  it("hides the badge when there are no pending approvals", () => {
    render(
      <RightRail
        activeTab="health"
        onTabChange={vi.fn()}
        approvalCount={0}
        alertCount={0}
        {...nodes}
      />,
    );
    expect(screen.getByText("HEALTH")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("hides the alerts tab when alerts are not available", () => {
    render(
      <RightRail
        activeTab="approvals"
        onTabChange={vi.fn()}
        approvalCount={0}
        alertCount={3}
        showAlerts={false}
        {...nodes}
      />,
    );
    expect(screen.queryByRole("tab", { name: /Alerts/ })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeInTheDocument();
  });
});
