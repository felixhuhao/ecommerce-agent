import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HealthPanel } from "./HealthPanel";

describe("HealthPanel", () => {
  it("renders explicit unavailable rows when health endpoints fail before data loads", () => {
    render(<HealthPanel healthUnavailable mcpUnavailable />);

    expect(screen.getByText("API")).toBeInTheDocument();
    expect(screen.getByText("health endpoint unavailable")).toBeInTheDocument();
    expect(screen.getByText("MCP health")).toBeInTheDocument();
    expect(screen.getByText("health/mcp endpoint unavailable")).toBeInTheDocument();
    expect(screen.getByText("api unavailable")).toBeInTheDocument();
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

    expect(screen.getByText("Mongo")).toBeInTheDocument();
    expect(screen.getByText("deepseek-chat")).toBeInTheDocument();
    expect(screen.getByText("spring")).toBeInTheDocument();
    expect(screen.getByText("11 tools")).toBeInTheDocument();
    expect(screen.queryByText("health endpoint unavailable")).not.toBeInTheDocument();
  });
});
