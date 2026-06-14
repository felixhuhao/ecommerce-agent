import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  acknowledgeAlert,
  approveApproval,
  createSession,
  getArtifacts,
  getMe,
  getMcpHealth,
  getTrace,
  listAlerts,
  listSessions,
  login,
  logout,
  postMessage,
  runMonitor,
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
  it("getMe returns null on unauthenticated responses", async () => {
    mockFetch(401, { detail: "not authenticated" });
    expect(await getMe()).toBeNull();
  });

  it("login posts credentials with cookies enabled", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ username: "alice", role: "operator" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await login("alice", "pw");

    expect(fetchMock).toHaveBeenCalledWith("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: "alice", password: "pw" }),
      credentials: "include",
    });
  });

  it("logout posts with cookies enabled", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await logout();

    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", {
      method: "POST",
      credentials: "include",
    });
  });

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

  it("approveApproval throws ApiError on non-conflict failures", async () => {
    mockFetch(500, { detail: "boom" });
    await expect(approveApproval("s1", "a1")).rejects.toMatchObject({ status: 500 });
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
    expect(fetchMock).toHaveBeenCalledWith("/health/mcp", { credentials: "include" });
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

  it("listAlerts returns alerts from the alert center endpoint", async () => {
    mockFetch(200, {
      alerts: [
        {
          alert_id: "a1",
          check_name: "low_stock",
          dedupe_key: "low_stock:SKU-9",
          title: "Low stock",
          severity: "warning",
          status: "open",
          metric: "inventory",
          value: 12,
          threshold: 50,
          entities: {},
          cause: null,
          grounding: { authority: "authoritative", sources: [], diagnostic: null },
          created_at: "x",
          updated_at: "x",
          acknowledged_at: null,
          acknowledged_by: null,
        },
      ],
    });

    const alerts = await listAlerts("open");
    expect(alerts[0].alert_id).toBe("a1");
    expect(fetch).toHaveBeenCalledWith("/api/alerts?status=open", { credentials: "include" });
  });

  it("acknowledgeAlert posts to the alert endpoint", async () => {
    mockFetch(200, {
      alert: {
        alert_id: "a1",
        check_name: "low_stock",
        dedupe_key: "low_stock:SKU-9",
        title: "Low stock",
        severity: "warning",
        status: "acknowledged",
        metric: "inventory",
        value: 12,
        threshold: 50,
        entities: {},
        cause: null,
        grounding: { authority: "authoritative", sources: [], diagnostic: null },
        created_at: "x",
        updated_at: "x",
        acknowledged_at: "x",
        acknowledged_by: "op1",
      },
    });

    const alert = await acknowledgeAlert("a1");
    expect(alert.status).toBe("acknowledged");
    expect(fetch).toHaveBeenCalledWith("/api/alerts/a1/acknowledge", {
      method: "POST",
      credentials: "include",
    });
  });

  it("runMonitor treats already-running 409 as a result", async () => {
    mockFetch(409, { detail: { status: "already_running" } });
    await expect(runMonitor()).resolves.toEqual({ status: "already_running" });
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
