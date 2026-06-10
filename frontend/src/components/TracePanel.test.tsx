import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { TracePanel } from "./TracePanel";
import type { TraceTimeline } from "../types";

function timeline(overrides: Partial<TraceTimeline> = {}): TraceTimeline {
  return {
    trace_id: "tr",
    session_id: "s1",
    turn_id: "t1",
    started_at: 1,
    ended_at: 2,
    duration_ms: 1200,
    tokens_in_total: 10,
    tokens_out_total: 20,
    span_count: 1,
    spans: [
      {
        kind: "tool_call",
        name: "generate_line_chart",
        status: "ok",
        ts: 1,
        duration_ms: 12,
        args_summary: "series",
        result_summary: "data:image/...",
        tokens_in: null,
        tokens_out: null,
        span_id: "x1",
        artifact_id: "chart-x1",
        approval_id: null,
        error_message: null,
      },
    ],
    ...overrides,
  };
}

const base = {
  inspectedTurnId: "t1" as string | null,
  isLoading: false,
  isError: false,
  exportHref: "/api/sessions/s1/turns/t1/trace/export",
  onViewArtifacts: vi.fn(),
  onViewApproval: vi.fn(),
};

describe("TracePanel", () => {
  it("renders spans, duration and the export link", () => {
    render(<TracePanel {...base} timeline={timeline()} />);
    expect(screen.getByText("generate_line_chart")).toBeInTheDocument();
    expect(screen.getByText("12 ms")).toBeInTheDocument();
    expect(screen.getByText("1 spans")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /JSON/i })).toHaveAttribute(
      "href",
      "/api/sessions/s1/turns/t1/trace/export",
    );
  });

  it("links an artifact span to the Artifacts tab", () => {
    const onViewArtifacts = vi.fn();
    render(<TracePanel {...base} onViewArtifacts={onViewArtifacts} timeline={timeline()} />);
    fireEvent.click(screen.getByRole("button", { name: /View in Artifacts/i }));
    expect(onViewArtifacts).toHaveBeenCalled();
  });

  it("links an approval span to its specific card by id", () => {
    const onViewApproval = vi.fn();
    render(
      <TracePanel
        {...base}
        onViewApproval={onViewApproval}
        timeline={timeline({
          span_count: 1,
          spans: [
            {
              kind: "tool_call",
              name: "request_approval",
              status: "ok",
              ts: 1,
              duration_ms: 5,
              args_summary: null,
              result_summary: null,
              tokens_in: null,
              tokens_out: null,
              span_id: "rq",
              artifact_id: null,
              approval_id: "appr-7",
              error_message: null,
            },
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /View approval/i }));
    expect(onViewApproval).toHaveBeenCalledWith("appr-7");
  });

  it("prompts to select a turn when none is inspected", () => {
    render(<TracePanel {...base} inspectedTurnId={null} timeline={undefined} />);
    expect(screen.getByText(/Select an answer's Inspect/i)).toBeInTheDocument();
  });

  it("shows empty-activity and error states", () => {
    const { rerender } = render(
      <TracePanel {...base} timeline={timeline({ spans: [], span_count: 0 })} />,
    );
    expect(screen.getByText(/No tool or model activity/i)).toBeInTheDocument();
    rerender(<TracePanel {...base} isError timeline={undefined} />);
    expect(screen.getByText(/Could not load trace/i)).toBeInTheDocument();
  });
});
