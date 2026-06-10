import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactPanel } from "./ArtifactPanel";
import type { ArtifactSummary } from "../types";

function artifact(overrides: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return {
    id: "c0",
    kind: "image",
    mime_type: "image/png",
    src: "data:image/png;base64,AAAA",
    tool_name: "generate_bar_chart",
    turn_id: "t1",
    trace_id: "tr",
    created_at: "2026-06-10T00:00:00Z",
    message_id: "m1",
    ...overrides,
  };
}

const base = { isLoading: false, isError: false, onJumpToMessage: vi.fn() };

describe("ArtifactPanel", () => {
  it("renders a card with a download link named by mime", () => {
    render(<ArtifactPanel {...base} artifacts={[artifact()]} />);
    const link = screen.getByRole("link", { name: /Download/i });
    expect(link).toHaveAttribute("download", "c0.png");
    expect(link).toHaveAttribute("href", "data:image/png;base64,AAAA");
  });

  it("fires onJumpToMessage with the owning message id", () => {
    const onJumpToMessage = vi.fn();
    render(<ArtifactPanel {...base} onJumpToMessage={onJumpToMessage} artifacts={[artifact()]} />);
    fireEvent.click(screen.getByRole("button", { name: /Jump to message/i }));
    expect(onJumpToMessage).toHaveBeenCalledWith("m1");
  });

  it("shows the empty state", () => {
    render(<ArtifactPanel {...base} artifacts={[]} />);
    expect(screen.getByText(/No charts generated/i)).toBeInTheDocument();
  });

  it("shows the error state", () => {
    render(<ArtifactPanel {...base} isError artifacts={[]} />);
    expect(screen.getByText(/Could not load artifacts/i)).toBeInTheDocument();
  });
});
