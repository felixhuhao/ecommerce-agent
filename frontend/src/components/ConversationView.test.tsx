import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationView } from "./ConversationView";
import type { ThreadMessage } from "../types";

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
});
