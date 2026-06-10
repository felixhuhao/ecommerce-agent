import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";

const nodes = {
  approvals: <div>APPROVALS</div>,
  artifacts: <div>ARTIFACTS</div>,
  trace: <div>TRACE</div>,
  health: <div>HEALTH</div>,
};

describe("RightRail", () => {
  it("renders only the active tab's panel and a pending badge", () => {
    render(<RightRail activeTab="approvals" onTabChange={vi.fn()} approvalCount={2} {...nodes} />);
    expect(screen.getByText("APPROVALS")).toBeInTheDocument();
    expect(screen.queryByText("TRACE")).not.toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("switches tab on click", () => {
    const onTabChange = vi.fn();
    render(<RightRail activeTab="approvals" onTabChange={onTabChange} approvalCount={0} {...nodes} />);
    fireEvent.click(screen.getByRole("tab", { name: "Trace" }));
    expect(onTabChange).toHaveBeenCalledWith("trace");
  });

  it("hides the badge when there are no pending approvals", () => {
    render(<RightRail activeTab="health" onTabChange={vi.fn()} approvalCount={0} {...nodes} />);
    expect(screen.getByText("HEALTH")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
