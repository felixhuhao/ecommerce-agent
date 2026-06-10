import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  approveApproval,
  createSession,
  getArtifacts,
  getMcpHealth,
  getTrace,
  listSessions,
  postMessage,
  shouldRetryTrace,
  traceExportUrl,
} from "./client";

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

  it("getTrace returns the timeline", async () => {
    mockFetch(200, {
      trace_id: "tr",
      session_id: "s1",
      turn_id: "t1",
      started_at: 1,
      ended_at: 2,
      duration_ms: 1,
      tokens_in_total: null,
      tokens_out_total: null,
      span_count: 0,
      spans: [],
    });

    const timeline = await getTrace("s1", "t1");
    expect(timeline.turn_id).toBe("t1");
    expect(timeline.spans).toEqual([]);
  });

  it("getTrace throws an ApiError carrying the status on 404", async () => {
    mockFetch(404, { detail: "trace not found" });
    await expect(getTrace("s1", "missing")).rejects.toMatchObject({ status: 404 });
  });

  it("getArtifacts returns the artifacts array", async () => {
    mockFetch(200, {
      session_id: "s1",
      artifacts: [
        {
          id: "c0",
          kind: "image",
          mime_type: "image/png",
          src: "data:image/png;base64,AA",
          tool_name: "generate_bar_chart",
          turn_id: "t1",
          trace_id: "tr",
          created_at: "x",
          message_id: "m1",
        },
      ],
    });

    const artifacts = await getArtifacts("s1");
    expect(artifacts[0].id).toBe("c0");
  });

  it("traceExportUrl builds the export path", () => {
    expect(traceExportUrl("s1", "t1")).toBe("/api/sessions/s1/turns/t1/trace/export");
  });

  it("shouldRetryTrace retries only 404 within the grace window", () => {
    expect(shouldRetryTrace(0, new ApiError(404))).toBe(true);
    expect(shouldRetryTrace(2, new ApiError(404))).toBe(true);
    expect(shouldRetryTrace(3, new ApiError(404))).toBe(false);
    expect(shouldRetryTrace(0, new ApiError(500))).toBe(false);
    expect(shouldRetryTrace(0, new Error("boom"))).toBe(false);
  });
});
