import type { HealthStatus, McpHealth, SessionDetail, SessionSummary, ThreadMessage } from "../types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status}`);
  return (await res.json()) as T;
}

export async function createSession(): Promise<{ session_id: string }> {
  return json(await fetch("/api/sessions", { method: "POST" }));
}

export async function listSessions(): Promise<SessionSummary[]> {
  const body = await json<{ sessions: SessionSummary[] }>(await fetch("/api/sessions"));
  return body.sessions;
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return json(await fetch(`/api/sessions/${sessionId}`));
}

export async function getThread(sessionId: string): Promise<ThreadMessage[]> {
  const body = await json<{ messages: ThreadMessage[] }>(
    await fetch(`/api/sessions/${sessionId}/thread`),
  );
  return body.messages;
}

export type SendResult = { turnInProgress: true } | { turnInProgress: false; turnId: string };

export async function postMessage(sessionId: string, message: string): Promise<SendResult> {
  const res = await fetch(`/api/sessions/${sessionId}/messages`, {
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
  const res = await fetch(url, init);
  const body = await res.json().catch(() => null);
  if (res.status === 409) return { conflict: true, body };
  if (!res.ok) throw new Error(`${res.status}`);
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
  return json(await fetch("/health"));
}

export async function getMcpHealth(): Promise<McpHealth> {
  return json(await fetch("/health/mcp"));
}
