import type {
  Alert,
  ArtifactSummary,
  HealthStatus,
  McpHealth,
  SessionDetail,
  SessionSummary,
  ThreadMessage,
  TraceTimeline,
} from "../types";

export class ApiError extends Error {
  constructor(readonly status: number) {
    super(String(status));
    this.name = "ApiError";
  }
}

export interface Me {
  user_id: string;
  username: string;
  role: "viewer" | "operator";
  spring_user_id: number;
}

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: "include" });
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ApiError(res.status);
  return (await res.json()) as T;
}

export async function getMe(): Promise<Me | null> {
  const res = await apiFetch("/api/auth/me");
  if (res.status === 401) return null;
  return json<Me>(res);
}

export async function login(username: string, password: string): Promise<Me> {
  const res = await apiFetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (res.status === 401) throw new Error("invalid credentials");
  return json<Me>(res);
}

export async function logout(): Promise<void> {
  await apiFetch("/api/auth/logout", { method: "POST" });
}

export async function createSession(): Promise<{ session_id: string }> {
  return json(await apiFetch("/api/sessions", { method: "POST" }));
}

export async function listSessions(): Promise<SessionSummary[]> {
  const body = await json<{ sessions: SessionSummary[] }>(await apiFetch("/api/sessions"));
  return body.sessions;
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return json(await apiFetch(`/api/sessions/${sessionId}`));
}

export async function getThread(sessionId: string): Promise<ThreadMessage[]> {
  const body = await json<{ messages: ThreadMessage[] }>(
    await apiFetch(`/api/sessions/${sessionId}/thread`),
  );
  return body.messages;
}

export type SendResult = { turnInProgress: true } | { turnInProgress: false; turnId: string };

export async function postMessage(sessionId: string, message: string): Promise<SendResult> {
  const res = await apiFetch(`/api/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (res.status === 409) return { turnInProgress: true };

  const body = await json<{ turn_id: string }>(res);
  return { turnInProgress: false, turnId: body.turn_id };
}

export type ApprovalActionResult = { conflict: boolean; body: unknown };

async function approvalAction(url: string, init: RequestInit): Promise<ApprovalActionResult> {
  const res = await apiFetch(url, init);
  const body = await res.json().catch(() => null);
  if (res.status === 409) return { conflict: true, body };
  if (!res.ok) throw new ApiError(res.status);
  return { conflict: false, body };
}

export async function approveApproval(
  sessionId: string,
  approvalId: string,
): Promise<ApprovalActionResult> {
  return approvalAction(`/api/sessions/${sessionId}/approvals/${approvalId}/approve`, {
    method: "POST",
  });
}

export async function rejectApproval(
  sessionId: string,
  approvalId: string,
  reason?: string,
): Promise<ApprovalActionResult> {
  return approvalAction(`/api/sessions/${sessionId}/approvals/${approvalId}/reject`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ reason }),
  });
}

export async function getHealth(): Promise<HealthStatus> {
  return json(await apiFetch("/health"));
}

export async function getMcpHealth(): Promise<McpHealth> {
  return json(await apiFetch("/health/mcp"));
}

export async function getTrace(sessionId: string, turnId: string): Promise<TraceTimeline> {
  return json(await apiFetch(`/api/sessions/${sessionId}/turns/${turnId}/trace`));
}

export async function getArtifacts(sessionId: string): Promise<ArtifactSummary[]> {
  const body = await json<{ artifacts: ArtifactSummary[] }>(
    await apiFetch(`/api/sessions/${sessionId}/artifacts`),
  );
  return body.artifacts;
}

export async function listAlerts(status?: string): Promise<Alert[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
  const body = await json<{ alerts: Alert[] }>(await apiFetch(`/api/alerts${suffix}`));
  return body.alerts;
}

export async function acknowledgeAlert(alertId: string): Promise<Alert> {
  const body = await json<{ alert: Alert }>(
    await apiFetch(`/api/alerts/${alertId}/acknowledge`, { method: "POST" }),
  );
  return body.alert;
}

export interface MonitorRunResult {
  status: string;
  created_count?: number;
  skipped_count?: number;
  errors?: { check: string; error: string }[];
}

export async function runMonitor(): Promise<MonitorRunResult> {
  const res = await apiFetch("/api/monitor/run", { method: "POST" });
  if (res.status === 409) {
    const body = await res.json().catch(() => ({ detail: { status: "already_running" } }));
    return body.detail ?? { status: "already_running" };
  }
  return json<MonitorRunResult>(res);
}

export function traceExportUrl(sessionId: string, turnId: string): string {
  return `/api/sessions/${sessionId}/turns/${turnId}/trace/export`;
}

// Absorb the brief post-answer window before the trace store/cache is readable.
export function shouldRetryTrace(failureCount: number, error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && failureCount < 3;
}
