import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

afterEach(() => {
  vi.restoreAllMocks();
});

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App", () => {
  it("renders the operator console panes on an empty backend state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/sessions") {
          return new Response(JSON.stringify({ sessions: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        if (url === "/health") {
          return new Response(
            JSON.stringify({
              status: "ok",
              app: "ecommerce-agent",
              environment: "test",
              configured_mcp_servers: ["spring"],
              agent_ready: true,
              components: {
                mongo: { status: "ok" },
                sandbox: { status: "ok" },
                model: { status: "unconfigured" },
              },
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        if (url === "/health/mcp") {
          return new Response(
            JSON.stringify({ status: "ok", servers: { spring: { status: "ok", tool_count: 14 } } }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        return new Response("not found", { status: 404 });
      }),
    );

    renderApp();

    expect(await screen.findByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("Conversation")).toBeInTheDocument();
    expect(screen.getByText("Approvals")).toBeInTheDocument();
    expect(await screen.findByText("System")).toBeInTheDocument();
  });
});
