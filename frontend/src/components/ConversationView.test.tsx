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
});
