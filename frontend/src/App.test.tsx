import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { SessionSummary, ThreadMessage } from "./types";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  FakeEventSource.sources = [];
});

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

class FakeEventSource {
  static sources: FakeEventSource[] = [];

  close = vi.fn();
  private listeners = new Map<string, Set<EventListener>>();

  constructor(readonly url: string) {
    FakeEventSource.sources.push(this);
  }

  addEventListener(name: string, listener: EventListener) {
    const listeners = this.listeners.get(name) ?? new Set<EventListener>();
    listeners.add(listener);
    this.listeners.set(name, listeners);
  }

  removeEventListener(name: string, listener: EventListener) {
    this.listeners.get(name)?.delete(listener);
  }

  emit(name: string, data: unknown) {
    const event = new MessageEvent(name, { data: JSON.stringify(data) });
    this.listeners.get(name)?.forEach((listener) => listener(event));
  }
}

function threadMessage(overrides: Partial<ThreadMessage> = {}): ThreadMessage {
  return {
    message_id: "m1",
    session_id: "s1",
    seq: 1,
    type: "agent_answer",
    content: "Done",
    created_at: "2026-06-10T00:00:00Z",
    turn_id: null,
    trace_id: null,
    actor_id: null,
    execution_id: null,
    approval_id: null,
    card: null,
    tool_name: null,
    status: null,
    result: null,
    grounding: null,
    reason: null,
    ...overrides,
  };
}

const AUTH_ME = {
  user_id: "alice",
  username: "alice",
  role: "operator",
  spring_user_id: 7,
};

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
        if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
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
    expect(screen.getByRole("tab", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Trace" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Health" }));
    expect(await screen.findByText("System")).toBeInTheDocument();
  });

  it("ignores stale send completion after switching sessions", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sessions: SessionSummary[] = [
      {
        session_id: "s1",
        title: "Session One",
        created_at: "2026-06-10T00:00:00Z",
        last_message_preview: null,
        message_count: 0,
      },
      {
        session_id: "s2",
        title: "Session Two",
        created_at: "2026-06-10T00:01:00Z",
        last_message_preview: null,
        message_count: 0,
      },
    ];
    const threads: Record<string, ThreadMessage[]> = { s1: [], s2: [] };
    const sendResponse = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/thread") return jsonResponse({ messages: threads.s1 });
      if (url === "/api/sessions/s2/thread") return jsonResponse({ messages: threads.s2 });
      if (url === "/api/sessions/s1/messages" && init?.method === "POST") {
        return sendResponse.promise;
      }
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
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 14 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const firstSession = await screen.findByRole("button", { name: /Session One/i });
    expect(firstSession).toHaveAttribute("aria-current", "page");

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "hello from one" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/sessions/s1/messages", expect.anything()));

    fireEvent.click(screen.getByRole("button", { name: /Session Two/i }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Session Two/i })).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );

    sendResponse.resolve(jsonResponse({ turn_id: "turn-s1" }, 202));
    await waitFor(() => expect(screen.getByLabelText("Message")).not.toBeDisabled());
    expect(screen.queryByText("streaming")).not.toBeInTheDocument();
  });

  it("ignores stale approval reconciliation after switching sessions", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sessions: SessionSummary[] = [
      {
        session_id: "s1",
        title: "Session One",
        created_at: "2026-06-10T00:00:00Z",
        last_message_preview: null,
        message_count: 1,
      },
      {
        session_id: "s2",
        title: "Session Two",
        created_at: "2026-06-10T00:01:00Z",
        last_message_preview: null,
        message_count: 0,
      },
    ];
    const staleProposal = threadMessage({
      message_id: "proposal-1",
      type: "agent_proposal",
      content: "Create a purchase order",
      approval_id: "approval-1",
      card: { product: "Coffee" },
      tool_name: "purchase_order_create",
      status: "pending",
    });
    const getThreadResponse = deferred<Response>();
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/approvals/approval-1/approve" && init?.method === "POST") {
        return jsonResponse({ detail: "stale" }, 409);
      }
      if (url === "/api/sessions/s1/thread") return getThreadResponse.promise;
      if (url === "/api/sessions/s2/thread") return jsonResponse({ messages: [] });
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
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 14 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await waitFor(() =>
      expect(FakeEventSource.sources.some((source) => source.url === "/api/sessions/s1/stream")).toBe(
        true,
      ),
    );
    const firstStream = FakeEventSource.sources.find(
      (source) => source.url === "/api/sessions/s1/stream",
    );
    act(() => firstStream?.emit("thread.append", { message: staleProposal }));
    expect(await screen.findAllByText("approval-1")).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Approve" })[0]);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/sessions/s1/approvals/approval-1/approve",
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/sessions/s1/thread", {
        credentials: "include",
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: /Session Two/i }));
    getThreadResponse.resolve(jsonResponse({ messages: [staleProposal] }));

    await waitFor(() => expect(screen.queryByText("approval-1")).not.toBeInTheDocument());
  });

  it("inspecting an answer opens its trace in the rail", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sessions: SessionSummary[] = [
      {
        session_id: "s1",
        title: "S1",
        created_at: "2026-06-10T00:00:00Z",
        last_message_preview: null,
        message_count: 1,
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/thread") return jsonResponse({ messages: [] });
      if (url === "/api/sessions/s1/artifacts") {
        return jsonResponse({ session_id: "s1", artifacts: [] });
      }
      if (url === "/api/sessions/s1/turns/turn-1/trace") {
        return jsonResponse({
          trace_id: "tr",
          session_id: "s1",
          turn_id: "turn-1",
          started_at: 1,
          ended_at: 2,
          duration_ms: 5,
          tokens_in_total: null,
          tokens_out_total: null,
          span_count: 1,
          spans: [
            {
              kind: "tool_call",
              name: "order_query",
              status: "ok",
              ts: 1,
              duration_ms: 3,
              args_summary: null,
              result_summary: null,
              evidence: null,
              tokens_in: null,
              tokens_out: null,
              span_id: "x",
              artifact_id: null,
              approval_id: null,
              error_message: null,
            },
          ],
        });
      }
      if (url === "/health") {
        return jsonResponse({
          status: "ok",
          app: "a",
          environment: "t",
          configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: { mongo: { status: "ok" }, sandbox: { status: "ok" }, model: { status: "ok" } },
        });
      }
      if (url === "/health/mcp") {
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 1 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await waitFor(() =>
      expect(FakeEventSource.sources.some((source) => source.url === "/api/sessions/s1/stream")).toBe(
        true,
      ),
    );
    const stream = FakeEventSource.sources.find((source) => source.url === "/api/sessions/s1/stream");
    act(() =>
      stream?.emit("thread.append", {
        message: threadMessage({
          message_id: "m1",
          type: "agent_answer",
          content: "done",
          turn_id: "turn-1",
        }),
      }),
    );

    fireEvent.click(await screen.findByRole("button", { name: /Inspect/i }));
    expect(await screen.findByText("order_query")).toBeInTheDocument();
  });

  it("keeps the active rail tab when a pending proposal arrives inline", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
    const sessions: SessionSummary[] = [
      {
        session_id: "s1",
        title: "S1",
        created_at: "2026-06-10T00:00:00Z",
        last_message_preview: null,
        message_count: 0,
      },
    ];
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/thread") return jsonResponse({ messages: [] });
      if (url === "/api/sessions/s1/artifacts") {
        return jsonResponse({ session_id: "s1", artifacts: [] });
      }
      if (url === "/health") {
        return jsonResponse({
          status: "ok",
          app: "a",
          environment: "t",
          configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: { mongo: { status: "ok" }, sandbox: { status: "ok" }, model: { status: "ok" } },
        });
      }
      if (url === "/health/mcp") {
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 1 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await waitFor(() =>
      expect(FakeEventSource.sources.some((source) => source.url === "/api/sessions/s1/stream")).toBe(
        true,
      ),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Trace" }));
    expect(screen.getByRole("tab", { name: "Trace" })).toHaveAttribute("aria-selected", "true");

    const stream = FakeEventSource.sources.find((source) => source.url === "/api/sessions/s1/stream");
    act(() =>
      stream?.emit("thread.append", {
        message: threadMessage({
          message_id: "proposal-1",
          seq: 2,
          type: "agent_proposal",
          content: "Create a purchase order",
          approval_id: "approval-1",
          card: { title: "Create purchase order", totalCost: 9000 },
          tool_name: "purchase_order_create",
          status: "pending",
        }),
      }),
    );

    expect(screen.getByRole("tab", { name: "Trace" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByLabelText("Approval card")).toBeInTheDocument();
    expect(screen.getByText("Create purchase order")).toBeInTheDocument();
    expect(screen.getByText("approval-1")).toBeInTheDocument();
  });

  it("refetches artifacts when a running turn finishes", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const sessions: SessionSummary[] = [
      {
        session_id: "s1",
        title: "S1",
        created_at: "2026-06-10T00:00:00Z",
        last_message_preview: null,
        message_count: 0,
      },
    ];
    let artifactFetches = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return jsonResponse(AUTH_ME);
      if (url === "/api/sessions") return jsonResponse({ sessions });
      if (url === "/api/sessions/s1/thread") return jsonResponse({ messages: [] });
      if (url === "/api/sessions/s1/artifacts") {
        artifactFetches += 1;
        return jsonResponse({ session_id: "s1", artifacts: [] });
      }
      if (url === "/api/sessions/s1/messages" && init?.method === "POST") {
        return jsonResponse({ turn_id: "turn-1", user_message_id: "m-user" }, 202);
      }
      if (url === "/health") {
        return jsonResponse({
          status: "ok",
          app: "a",
          environment: "t",
          configured_mcp_servers: ["spring"],
          agent_ready: true,
          components: { mongo: { status: "ok" }, sandbox: { status: "ok" }, model: { status: "ok" } },
        });
      }
      if (url === "/health/mcp") {
        return jsonResponse({ status: "ok", servers: { spring: { status: "ok", tool_count: 1 } } });
      }
      return new Response("not found", { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await waitFor(() => expect(artifactFetches).toBe(1));
    await waitFor(() =>
      expect(FakeEventSource.sources.some((source) => source.url === "/api/sessions/s1/stream")).toBe(
        true,
      ),
    );

    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "make chart" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/sessions/s1/messages", expect.anything()));

    const stream = FakeEventSource.sources.find((source) => source.url === "/api/sessions/s1/stream");
    act(() => stream?.emit("done", { turn_id: "turn-1" }));

    await waitFor(() => expect(artifactFetches).toBeGreaterThanOrEqual(2));
  });
});
