import type { ThreadMessage } from "../types";

export interface ApprovalView {
  approvalId: string;
  card: Record<string, unknown> | null;
  toolName: string | null;
  status: string;
  result: Record<string, unknown> | null;
  reason: string | null;
}

export function foldApprovals(messages: ThreadMessage[]): ApprovalView[] {
  const byId = new Map<string, ApprovalView>();

  for (const message of [...messages].sort((a, b) => a.seq - b.seq)) {
    if (!message.approval_id) continue;
    const prev = byId.get(message.approval_id) ?? {
      approvalId: message.approval_id,
      card: null,
      toolName: null,
      status: "pending",
      result: null,
      reason: null,
    };

    byId.set(message.approval_id, {
      ...prev,
      card: message.card ?? prev.card,
      toolName: message.tool_name ?? prev.toolName,
      status: message.status ?? prev.status,
      result: message.result ?? prev.result,
      reason: message.reason ?? prev.reason,
    });
  }

  return [...byId.values()];
}
