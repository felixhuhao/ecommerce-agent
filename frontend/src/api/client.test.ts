import { afterEach, describe, expect, it, vi } from "vitest";
import { approveApproval, createSession, getMcpHealth, listSessions, postMessage } from "./client";

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })),
  );
}

describe("api client", () => {
  it("createSession returns the session id", async () => {
    mockFetch(201, { session_id: "abc" });
    expect(await createSession()).toEqual({ session_id: "abc" });
  });

  it("listSessions returns the summaries array", async () => {
    mockFetch(200, {
      sessions: [
        {
          session_id: "s1",
          title: "t",
          created_at: "x",
          last_message_preview: null,
          message_count: 0,
        },
      ],
    });
    const sessions = await listSessions();
    expect(sessions[0].session_id).toBe("s1");
  });

  it("postMessage flags turn_in_progress on 409", async () => {
    mockFetch(409, { detail: { error: "turn_in_progress" } });
    expect(await postMessage("s1", "hi")).toEqual({ turnInProgress: true });
  });

  it("postMessage returns the turn id on 202", async () => {
    mockFetch(202, { turn_id: "t1", user_message_id: "m1" });
    expect(await postMessage("s1", "hi")).toEqual({ turnInProgress: false, turnId: "t1" });
  });

  it("approveApproval reports a conflict on 409 instead of throwing", async () => {
    mockFetch(409, { detail: "already decided" });
    expect(await approveApproval("s1", "a1")).toEqual({
      conflict: true,
      body: { detail: "already decided" },
    });
  });

  it("getMcpHealth fetches /health/mcp", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ status: "ok", servers: { spring: { status: "ok" } } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    expect(await getMcpHealth()).toEqual({ status: "ok", servers: { spring: { status: "ok" } } });
    expect(fetchMock).toHaveBeenCalledWith("/health/mcp");
  });
});
