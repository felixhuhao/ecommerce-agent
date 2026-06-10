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
  onFocusApprovalHandled?: () => void;
}

// Keys already shown in the card header — don't repeat them in the body.
const HEADER_KEYS = new Set(["approvalId", "toolName", "status", "operationType"]);
const MONEY_KEY = /(?:cost|price|amount|total|subtotal|spend)/i;

// camelCase / snake_case → "Title Case" so labels read as prose, not field names.
function humanizeKey(key: string): string {
  return key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function isScalar(value: unknown): value is string | number | boolean {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean";
}

function isMoney(key: string, value: unknown): value is number {
  return typeof value === "number" && MONEY_KEY.test(key);
}

function formatScalar(key: string, value: string | number | boolean): string {
  if (isMoney(key, value)) {
    return value.toLocaleString(undefined, { style: "currency", currency: "USD" });
  }
  return String(value);
}

// Flatten a single-key object like { totalCost: 62.5 } to its inner scalar entry, so
// trivial objects render as one clean row (the inner key drives money detection).
function singleScalarEntry(
  value: unknown,
): { key: string; value: string | number | boolean } | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 1 && isScalar(entries[0][1])) {
      return { key: entries[0][0], value: entries[0][1] };
    }
  }
  return null;
}

function cardEntries(card: Record<string, unknown> | null) {
  if (!card) return [];
  return Object.entries(card)
    .filter(([key]) => !key.startsWith("_") && !HEADER_KEYS.has(key))
    .slice(0, 10);
}

export function ApprovalWorkspace({
  approvals,
  pendingApprovalId,
  actionError,
  onApprove,
  onReject,
  focusApprovalId,
  onFocusApprovalHandled,
}: ApprovalWorkspaceProps) {
  const [reasons, setReasons] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!focusApprovalId) return;
    document
      .querySelector(`[data-approval-id="${focusApprovalId}"]`)
      ?.scrollIntoView({ block: "center" });
    onFocusApprovalHandled?.();
  }, [focusApprovalId, onFocusApprovalHandled]);

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
          const operationType =
            typeof approval.card?.operationType === "string" ? approval.card.operationType : null;
          return (
            <article
              className="approval-card"
              key={approval.approvalId}
              data-approval-id={approval.approvalId}
            >
              <header className="approval-card-header">
                <div>
                  {operationType ? <span className="approval-op">{operationType}</span> : null}
                  <span className="approval-tool">{approval.toolName || "approval"}</span>
                  <span className="approval-id">{approval.approvalId}</span>
                </div>
                <span className={`status-pill status-${approval.status}`}>{approval.status}</span>
              </header>

              {cardEntries(approval.card).length > 0 ? (
                <dl className="kv-list">
                  {cardEntries(approval.card).map(([key, value]) => {
                    const flat = isScalar(value) ? { key, value } : singleScalarEntry(value);
                    if (flat) {
                      return (
                        <div key={key}>
                          <dt>{humanizeKey(key)}</dt>
                          <dd className={isMoney(flat.key, flat.value) ? "kv-money" : undefined}>
                            {formatScalar(flat.key, flat.value)}
                          </dd>
                        </div>
                      );
                    }
                    return (
                      <div key={key} className="kv-block">
                        <dt>{humanizeKey(key)}</dt>
                        <dd>
                          <pre className="kv-json">{JSON.stringify(value, null, 2)}</pre>
                        </dd>
                      </div>
                    );
                  })}
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
