import type { ThreadMessage } from "../types";

export interface ApprovalView {
  approvalId: string;
  card: Record<string, unknown> | null;
  toolName: string | null;
  status: string;
  result: Record<string, unknown> | null;
  reason: string | null;
}

interface ApprovalAccumulator {
  firstSeq: number;
  view: ApprovalView;
}

export function foldApprovals(messages: ThreadMessage[]): ApprovalView[] {
  const byId = new Map<string, ApprovalAccumulator>();

  for (const message of [...messages].sort((a, b) => a.seq - b.seq)) {
    if (!message.approval_id) continue;
    const prev = byId.get(message.approval_id) ?? {
      firstSeq: message.seq,
      view: {
        approvalId: message.approval_id,
        card: null,
        toolName: null,
        status: "pending",
        result: null,
        reason: null,
      },
    };

    byId.set(message.approval_id, {
      firstSeq: prev.firstSeq,
      view: {
        ...prev.view,
        card: message.card ?? prev.view.card,
        toolName: message.tool_name ?? prev.view.toolName,
        status: message.status ?? prev.view.status,
        result: message.result ?? prev.view.result,
        reason: message.reason ?? prev.view.reason,
      },
    });
  }

  return [...byId.values()]
    .sort((a, b) => b.firstSeq - a.firstSeq)
    .map(({ view }) => view);
}
