import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthPanel } from "./HealthPanel";

describe("HealthPanel", () => {
  it("renders explicit unavailable rows when health endpoints fail before data loads", () => {
    render(<HealthPanel healthUnavailable mcpUnavailable />);

    expect(screen.getByText("API")).toBeInTheDocument();
    expect(screen.getByText("health endpoint unavailable")).toBeInTheDocument();
    expect(screen.getByText("Tool gateway")).toBeInTheDocument();
    expect(screen.getByText("health/mcp endpoint unavailable")).toBeInTheDocument();
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
  });

  it("renders component and server health when data is available", () => {
    render(
      <HealthPanel
        health={{
          status: "ok",
          app: "ecommerce-agent",
          environment: "local",
          configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: {
            mongo: { status: "ok" },
            sandbox: { status: "ok" },
            model: { status: "ok", model: "deepseek-chat" },
          },
        }}
        mcp={{ status: "ok", servers: { spring: { status: "ok", tool_count: 11 } } }}
        healthUnavailable
        mcpUnavailable
      />,
    );

    expect(screen.getByText("Conversation store")).toBeInTheDocument();
    expect(screen.getByText("Analysis sandbox")).toBeInTheDocument();
    expect(screen.getByText("AI model")).toBeInTheDocument();
    expect(screen.getByText("deepseek-chat")).toBeInTheDocument();
    expect(screen.getByText("Commerce tools")).toBeInTheDocument();
    expect(screen.getByText("11 tools · spring")).toBeInTheDocument();
    expect(screen.getByText("Environment: local")).toBeInTheDocument();
    expect(screen.queryByText("health endpoint unavailable")).not.toBeInTheDocument();
  });
});
