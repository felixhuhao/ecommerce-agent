import type { ApprovalActionResult } from "../api/client";

interface ApproveApi {
  approveApproval: (sessionId: string, approvalId: string) => Promise<ApprovalActionResult>;
}

interface RejectApi {
  rejectApproval: (
    sessionId: string,
    approvalId: string,
    reason?: string,
  ) => Promise<ApprovalActionResult>;
}

export async function performApprove(
  sessionId: string,
  approvalId: string,
  api: ApproveApi,
  onConflict: () => void | Promise<void>,
): Promise<void> {
  const result = await api.approveApproval(sessionId, approvalId);
  if (result.conflict) await onConflict();
}

export async function performReject(
  sessionId: string,
  approvalId: string,
  reason: string | undefined,
  api: RejectApi,
  onConflict: () => void | Promise<void>,
): Promise<void> {
  const result = await api.rejectApproval(sessionId, approvalId, reason);
  if (result.conflict) await onConflict();
}
