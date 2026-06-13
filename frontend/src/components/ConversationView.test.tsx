import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "./ConversationView";
import type { ThreadMessage } from "../types";

function baseProps() {
  return {
    provisionalAnswer: null,
    activeTool: null,
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
        activeTool={null}
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
                  tool_name: "generate_line_chart",
                },
              ],
            },
          }),
        ]}
        provisionalAnswer={null}
        activeTool={null}
        streamStatus="open"
        composerDisabled={false}
        busyNote={null}
        error={null}
        onSend={vi.fn()}
      />,
    );

    const image = document.querySelector(".chart-artifact img");
    expect(image).toHaveAttribute("src", src);
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

  it("shows an Inspect control on agent answers that calls onInspect with the turn id", () => {
    const onInspect = vi.fn();
    render(
      <ConversationView {...baseProps()} onInspect={onInspect} messages={[message({ turn_id: "turn-9" })]} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Inspect/i }));
    expect(onInspect).toHaveBeenCalledWith("turn-9");
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
  });

  it("shows trace evidence in Sources when the inspected timeline is loaded", () => {
    render(
      <ConversationView
        {...baseProps()}
        inspectedTurnId="t1"
        messages={[
          message({
            turn_id: "t1",
            grounding: {
              authority: "derived",
              diagnostic: null,
              sources: [
                {
                  span_id: "span-1",
                  tool_name: "execute",
                  args_summary: null,
                  result_summary: "forecast summary",
                },
              ],
            },
          }),
        ]}
        traceTimeline={{
          trace_id: "tr",
          session_id: "s1",
          turn_id: "t1",
          started_at: 1,
          ended_at: 2,
          duration_ms: 10,
          tokens_in_total: null,
          tokens_out_total: null,
          span_count: 1,
          spans: [
            {
              kind: "tool_call",
              name: "execute",
              status: "ok",
              ts: 1,
              duration_ms: 3,
              args_summary: null,
              result_summary: "forecast summary",
              evidence: "full forecast evidence",
              tokens_in: null,
              tokens_out: null,
              span_id: "span-1",
              artifact_id: null,
              approval_id: null,
              error_message: null,
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByText("Sources (1)"));
    expect(screen.getByText(/full forecast evidence/)).toBeInTheDocument();
  });

  it("shows an Inspect control on agent proposals", () => {
    const onInspect = vi.fn();
    render(
      <ConversationView
        {...baseProps()}
        onInspect={onInspect}
        messages={[message({ type: "agent_proposal", turn_id: "proposal-turn" })]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Inspect/i }));
    expect(onInspect).toHaveBeenCalledWith("proposal-turn");
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

  it("shows no Inspect control on operator messages", () => {
    render(
      <ConversationView
        {...baseProps()}
        onInspect={vi.fn()}
        messages={[message({ type: "user", content: "hi", turn_id: null })]}
      />,
    );

    expect(screen.queryByRole("button", { name: /Inspect/i })).toBeNull();
  });

  it("clears handled message focus after scrolling", async () => {
    const scrollIntoView = vi.fn();
    const onFocusMessageHandled = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    render(
      <ConversationView
        {...baseProps()}
        messages={[message({ message_id: "m-focus" })]}
        focusMessageId="m-focus"
        onFocusMessageHandled={onFocusMessageHandled}
      />,
    );

    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ block: "center" }));
    expect(onFocusMessageHandled).toHaveBeenCalled();
  });
});
