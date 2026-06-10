import { useEffect, useState } from "react";
import { Check, X } from "lucide-react";
import type { ApprovalView } from "../state/approvals";

interface ApprovalWorkspaceProps {
  approvals: ApprovalView[];
  pendingApprovalId: string | null;
  actionError: string | null;
  onApprove: (approvalId: string) => Promise<void> | void;
  onReject: (approvalId: string, reason: string | undefined) => Promise<void> | void;
  focusApprovalId?: string | null;
}

function formatValue(value: unknown) {
  if (value == null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function cardEntries(card: Record<string, unknown> | null) {
  if (!card) return [];
  return Object.entries(card).filter(([key]) => !key.startsWith("_")).slice(0, 8);
}

export function ApprovalWorkspace({
  approvals,
  pendingApprovalId,
  actionError,
  onApprove,
  onReject,
  focusApprovalId,
}: ApprovalWorkspaceProps) {
  const [reasons, setReasons] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!focusApprovalId) return;
    document
      .querySelector(`[data-approval-id="${focusApprovalId}"]`)
      ?.scrollIntoView({ block: "center" });
  }, [focusApprovalId]);

  return (
    <section className="rail-panel approvals-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Governance</p>
          <h2>Approvals</h2>
        </div>
        <span className="count-badge">{approvals.length}</span>
      </div>

      {actionError ? <div className="notice notice-error">{actionError}</div> : null}

      <div className="approval-list">
        {approvals.length === 0 ? <p className="empty-note">No approvals</p> : null}
        {approvals.map((approval) => {
          const isPending = approval.status === "pending";
          const isBusy = pendingApprovalId === approval.approvalId;
          const reason = reasons[approval.approvalId] ?? "";
          return (
            <article
              className="approval-card"
              key={approval.approvalId}
              data-approval-id={approval.approvalId}
            >
              <header className="approval-card-header">
                <div>
                  <span className="approval-tool">{approval.toolName || "approval"}</span>
                  <span className="approval-id">{approval.approvalId}</span>
                </div>
                <span className={`status-pill status-${approval.status}`}>{approval.status}</span>
              </header>

              {cardEntries(approval.card).length > 0 ? (
                <dl className="kv-list">
                  {cardEntries(approval.card).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>{formatValue(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="empty-note">No card details</p>
              )}

              {approval.status === "consumed" && approval.result ? (
                <pre className="result-block">{JSON.stringify(approval.result, null, 2)}</pre>
              ) : null}
              {approval.status === "rejected" && approval.reason ? (
                <p className="state-note">{approval.reason}</p>
              ) : null}
              {approval.status === "invalidated" ? (
                <p className="state-note">Request a fresh approval.</p>
              ) : null}
              {approval.status === "failed" && approval.reason ? (
                <p className="state-note">{approval.reason}</p>
              ) : null}

              {approval.card ? (
                <details className="raw-details">
                  <summary>Raw details</summary>
                  <pre>{JSON.stringify(approval.card, null, 2)}</pre>
                </details>
              ) : null}

              <div className="approval-actions">
                <input
                  aria-label={`Reject reason for ${approval.approvalId}`}
                  value={reason}
                  onChange={(event) =>
                    setReasons((current) => ({
                      ...current,
                      [approval.approvalId]: event.currentTarget.value,
                    }))
                  }
                  disabled={!isPending || isBusy}
                />
                <button
                  className="icon-button danger"
                  type="button"
                  onClick={() => onReject(approval.approvalId, reason.trim() || undefined)}
                  disabled={!isPending || isBusy}
                  title="Reject"
                >
                  <X size={16} aria-hidden="true" />
                  <span className="sr-only">Reject</span>
                </button>
                <button
                  className="icon-button success"
                  type="button"
                  onClick={() => onApprove(approval.approvalId)}
                  disabled={!isPending || isBusy}
                  title="Approve"
                >
                  <Check size={16} aria-hidden="true" />
                  <span className="sr-only">Approve</span>
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
