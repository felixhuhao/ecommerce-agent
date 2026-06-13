import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
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

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("App auth shell", () => {
  it("shows the login form when unauthenticated", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/api/auth/me") return new Response(null, { status: 401 });
        return new Response("{}", { status: 200 });
      }),
    );

    renderApp();

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("returns to the login form immediately when logout is clicked", async () => {
    const logoutResponse = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") {
        return jsonResponse({
          user_id: "alice",
          username: "alice",
          role: "operator",
          spring_user_id: 7,
        });
      }
      if (url === "/api/sessions") return jsonResponse({ sessions: [] });
      if (url === "/health") {
        return jsonResponse({
          status: "ok",
          app: "ecommerce-agent",
          environment: "test",
          configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: {
            mongo: { status: "ok" },
            sandbox: { status: "ok" },
            model: { status: "ok" },
          },
        });
      }
      if (url === "/health/mcp") {
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok" } } });
      }
      if (url === "/api/auth/logout" && init?.method === "POST") {
        return logoutResponse.promise;
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: /log out/i }));

    expect(await screen.findByLabelText(/username/i)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
    logoutResponse.resolve(jsonResponse({ ok: true }));
  });
});
