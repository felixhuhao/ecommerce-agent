import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ThreadMessage } from "../types";
import { ConversationView } from "./ConversationView";

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: vi.fn(() => ({
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  })),
}));
vi.mock("echarts/charts", () => ({
  BarChart: {},
  LineChart: {},
  PieChart: {},
  ScatterChart: {},
}));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  LegendComponent: {},
  TitleComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({
  SVGRenderer: {},
}));

function baseProps() {
  return {
    provisionalAnswer: null,
    streamStatus: "open" as const,
    composerDisabled: false,
    busyNote: null,
    error: null,
    onSend: vi.fn(),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function message(overrides: Partial<ThreadMessage> = {}): ThreadMessage {
  return {
    message_id: "m1",
    session_id: "s1",
    seq: 1,
    type: "agent_answer",
    content: "Done",
    created_at: "2026-06-10T00:00:00Z",
    turn_id: "t1",
    trace_id: null,
    actor_id: null,
    execution_id: null,
    approval_id: null,
    card: null,
    tool_name: null,
    status: "ok",
    result: null,
    grounding: null,
    reason: null,
    ...overrides,
  };
}

describe("ConversationView", () => {
  it("scrolls to the newest content as messages render", async () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(
      <ConversationView
        messages={[message()]}
        provisionalAnswer={null}
        streamStatus="open"
        composerDisabled={false}
        busyNote={null}
        error={null}
        onSend={vi.fn()}
      />,
    );

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" }));
  });

  it("renders image artifacts from agent message results", () => {
    const src = "data:image/svg+xml;base64,PHN2Zy8+";

    render(
      <ConversationView
        messages={[
          message({
            result: {
              artifacts: [
                {
                  id: "chart-1",
                  kind: "image",
                  mime_type: "image/svg+xml",
                  src,
                  tool_name: "image_export",
                },
                {
                  id: "chart-2",
                  kind: "image",
                  src,
                  tool_name: "image_export",
                },
              ],
            },
          }),
        ]}
        provisionalAnswer={null}
        streamStatus="open"
        composerDisabled={false}
        busyNote={null}
        error={null}
        onSend={vi.fn()}
      />,
    );

    const image = document.querySelector(".chart-artifact img");
    expect(image).toHaveAttribute("src", src);
    const downloads = screen.getAllByRole("link", { name: /Download/i });
    expect(downloads[0]).toHaveAttribute(
      "download",
      "chart-1.svg",
    );
    expect(downloads[1]).toHaveAttribute("download", "chart-2.png");
  });

  it("renders ECharts artifacts from agent message results", async () => {
    render(
      <ConversationView
        messages={[
          message({
            result: {
              artifacts: [
                {
                  id: "chart-1",
                  kind: "echarts",
                  title: "Sales by Category",
                  chart_type: "bar",
                  x_axis: { label: "Category", type: "category" },
                  y_axis: { label: "Sales", type: "value", unit: "USD" },
                  series: [
                    {
                      name: "Sales",
                      data: [{ x: "Electronics", y: 75997 }],
                    },
                  ],
                  tool_name: "create_chart_spec",
                },
              ],
            },
          }),
        ]}
        provisionalAnswer={null}
        streamStatus="open"
        composerDisabled={false}
        busyNote={null}
        error={null}
        onSend={vi.fn()}
      />,
    );

    expect(await screen.findByRole("img", { name: "Sales by Category" })).toBeInTheDocument();
    expect(await screen.findByText("create_chart_spec")).toBeInTheDocument();
  });

  it("renders unsupported artifacts as compact fallback", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[
          message({
            result: {
              artifacts: [{ id: "a1", kind: "mystery" }],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Chart artifact unavailable")).toBeInTheDocument();
  });

  it("renders agent markdown (bold + GFM table) as HTML", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[
          message({ content: "**Bold** line\n\n| Category | Sales |\n|---|---|\n| Phones | 42 |" }),
        ]}
      />,
    );

    expect(document.querySelector(".message-md strong")).not.toBeNull();
    expect(document.querySelector(".message-md table")).not.toBeNull();
    expect(document.querySelector(".message-md th")?.textContent).toContain("Category");
  });

  it("renders operator messages as plain text, not markdown", () => {
    render(
      <ConversationView {...baseProps()} messages={[message({ type: "user", content: "**not bold**" })]} />,
    );

    expect(document.querySelector(".message-md")).toBeNull();
    expect(screen.getByText("**not bold**")).toBeInTheDocument();
  });

  it("renders a confidence badge and Sources expander", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[
          message({
            grounding: {
              authority: "authoritative",
              diagnostic: null,
              sources: [
                {
                  span_id: "span-1",
                  tool_name: "get_statistics",
                  args_summary: '{"metric":"sales"}',
                  result_summary: "sales rows",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Authoritative")).toBeInTheDocument();
    expect(screen.getByText("Sources (1)")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Sources (1)"));
    expect(screen.getByText("get_statistics")).toBeInTheDocument();
    expect(screen.getByText(/sales rows/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Trace/i })).toBeNull();
  });

  it("renders live turn status steps", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[]}
        turnProgress={[
          {
            turnId: "t1",
            stepId: "start:t1",
            kind: "start",
            label: "Starting turn",
            status: "done",
            detail: null,
            ts: null,
          },
          {
            turnId: "t1",
            stepId: "tool:stats",
            kind: "tool",
            label: "Reading sales data",
            status: "running",
            detail: "get_statistics",
            ts: 1,
          },
        ]}
      />,
    );

    expect(screen.getByLabelText("Turn status")).toBeInTheDocument();
    expect(screen.getByText("Starting turn")).toBeInTheDocument();
    expect(screen.getByText("Reading sales data")).toBeInTheDocument();
  });

  it("keeps proposal header label while still showing grounding sources", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[
          message({
            type: "agent_proposal",
            grounding: {
              authority: "unverified",
              diagnostic: null,
              sources: [
                {
                  span_id: "span-1",
                  tool_name: "inventory_query",
                  args_summary: '{"sku":"SKU-9"}',
                  result_summary: "stock rows",
                },
              ],
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("Proposal")).toBeInTheDocument();
    expect(screen.queryByText("Unverified")).toBeNull();
    expect(screen.getByText("Sources (1)")).toBeInTheDocument();
  });

  it("renders inline approval controls for proposal messages with cards", () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    render(
      <ConversationView
        {...baseProps()}
        onApprove={onApprove}
        onReject={onReject}
        messages={[
          message({
            type: "agent_proposal",
            approval_id: "approval-1",
            card: { title: "Create purchase order" },
            status: "pending",
          }),
        ]}
      />,
    );

    expect(screen.getByText("Approval card")).toBeInTheDocument();
    expect(screen.getByText("Create purchase order")).toBeInTheDocument();
    expect(screen.getByText("approval-1")).toBeInTheDocument();
    expect(screen.getAllByText("pending")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledWith("approval-1");
  });

  it("hides inline approval actions after a proposal is rejected", () => {
    render(
      <ConversationView
        {...baseProps()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        messages={[
          message({
            seq: 1,
            type: "agent_proposal",
            approval_id: "approval-1",
            card: { title: "Create purchase order" },
            status: "pending",
          }),
          message({
            seq: 2,
            type: "approval_status",
            approval_id: "approval-1",
            status: "rejected",
            reason: "too costly",
          }),
        ]}
      />,
    );

    expect(screen.getByText("Approval card")).toBeInTheDocument();
    expect(screen.getAllByText("rejected").length).toBeGreaterThan(0);
    expect(screen.queryByText("pending")).toBeNull();
    expect(screen.getByText("too costly")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });

  it("shows no Inspect control on thread messages", () => {
    render(
      <ConversationView
        {...baseProps()}
        messages={[
          message({ type: "user", content: "hi", turn_id: null }),
          message({ seq: 2, type: "agent_answer", content: "done", turn_id: "t1" }),
          message({ seq: 3, type: "agent_proposal", content: "proposal", turn_id: "t2" }),
        ]}
      />,
    );

    expect(screen.queryByRole("button", { name: /Inspect/i })).toBeNull();
  });

});
