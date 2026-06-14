import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TurnStatusTracker } from "./TurnStatusTracker";

describe("TurnStatusTracker", () => {
  it("compacts repeated labels and preserves a running status", () => {
    render(
      <TurnStatusTracker
        steps={[
          {
            turnId: "t1",
            stepId: "execute:1",
            kind: "tool",
            label: "Running analysis",
            status: "done",
            detail: "execute",
            ts: 1,
          },
          {
            turnId: "t1",
            stepId: "execute:2",
            kind: "tool",
            label: "Running analysis",
            status: "running",
            detail: "execute",
            ts: 2,
          },
        ]}
      />,
    );

    expect(screen.getAllByText("Running analysis")).toHaveLength(1);
    expect(screen.getByText("x2")).toBeInTheDocument();
    expect(screen.getByText("Running analysis").closest("li")).toHaveClass("status-running");
  });
});
