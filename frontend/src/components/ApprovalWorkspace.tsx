import { useEffect, useState, type ReactNode } from "react";
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

// Shorter / friendlier labels for a few verbose keys; everything else is humanized.
const LABEL_OVERRIDES: Record<string, string> = { financialImpact: "Estimated Spend" };

// camelCase / snake_case → "Title Case" so labels read as prose, not field names.
function humanizeKey(key: string): string {
  if (LABEL_OVERRIDES[key]) return LABEL_OVERRIDES[key];
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

function formatMoney(value: number): string {
  return value.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

function formatScalar(key: string, value: string | number | boolean): string {
  if (isMoney(key, value)) {
    return formatMoney(value);
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

// How deep to expand nested objects/arrays into rows before falling back to JSON.
const MAX_DEPTH = 4;

// Normalize an object/array into [label, key, value] triples for row rendering.
function entriesOf(value: object): [label: string, key: string, value: unknown][] {
  if (Array.isArray(value)) {
    return value.map((item, index) => [`#${index + 1}`, String(index), item]);
  }
  return Object.entries(value as Record<string, unknown>)
    .filter(([key]) => !key.startsWith("_"))
    .map(([key, val]) => [humanizeKey(key), key, val]);
}

// Render any value as labeled rows (no braces/quotes): scalars inline (money
// emphasized), nested objects/arrays recursively, single-scalar objects flattened.
// Pathologically deep values fall back to a contained JSON block.
function renderValue(label: string, rawKey: string, value: unknown, depth: number): ReactNode {
  if (value == null) {
    return (
      <div key={rawKey}>
        <dt>{label}</dt>
        <dd>—</dd>
      </div>
    );
  }
  if (isScalar(value)) {
    return (
      <div key={rawKey}>
        <dt>{label}</dt>
        <dd className={isMoney(rawKey, value) ? "kv-money" : undefined}>
          {formatScalar(rawKey, value)}
        </dd>
      </div>
    );
  }
  const flat = singleScalarEntry(value);
  if (flat) {
    return (
      <div key={rawKey}>
        <dt>{label}</dt>
        <dd className={isMoney(flat.key, flat.value) ? "kv-money" : undefined}>
          {formatScalar(flat.key, flat.value)}
        </dd>
      </div>
    );
  }
  if (depth < MAX_DEPTH && typeof value === "object") {
    const children = entriesOf(value);
    if (children.length === 0) {
      return (
        <div key={rawKey}>
          <dt>{label}</dt>
          <dd>—</dd>
        </div>
      );
    }
    return (
      <div key={rawKey} className="kv-block">
        <dt>{label}</dt>
        <dl className="kv-list kv-nested">
          {children.map(([childLabel, childKey, childValue]) =>
            renderValue(childLabel, childKey, childValue, depth + 1),
          )}
        </dl>
      </div>
    );
  }
  return (
    <div key={rawKey} className="kv-block">
      <dt>{label}</dt>
      <dd>
        <pre className="kv-json">{JSON.stringify(value, null, 2)}</pre>
      </dd>
    </div>
  );
}

function sectionHint(value: object): string {
  if (Array.isArray(value)) {
    return `${value.length} item${value.length === 1 ? "" : "s"}`;
  }
  const count = Object.keys(value).filter((key) => !key.startsWith("_")).length;
  return `${count} field${count === 1 ? "" : "s"}`;
}

// Top-level fields: scalars/money inline; multi-field objects & arrays become a
// collapsible section (open while the operator is still deciding, i.e. pending).
function renderTopEntry(label: string, rawKey: string, value: unknown, open: boolean): ReactNode {
  if (value == null || isScalar(value) || singleScalarEntry(value)) {
    return renderValue(label, rawKey, value, 0);
  }
  if (typeof value === "object") {
    const children = entriesOf(value);
    if (children.length === 0) {
      return renderValue(label, rawKey, value, 0);
    }
    return (
      <details key={rawKey} className="kv-section" open={open}>
        <summary>
          <span className="kv-section-label">{label}</span>
          <span className="kv-section-hint">{sectionHint(value)}</span>
        </summary>
        <dl className="kv-list kv-nested">
          {children.map(([childLabel, childKey, childValue]) =>
            renderValue(childLabel, childKey, childValue, 1),
          )}
        </dl>
      </details>
    );
  }
  return renderValue(label, rawKey, value, 0);
}

// The first top-level money field becomes the headline figure (and is dropped from the body).
function findMoneyHeadline(
  card: Record<string, unknown>,
): { key: string; label: string; value: number } | null {
  for (const [key, value] of Object.entries(card)) {
    if (key.startsWith("_") || HEADER_KEYS.has(key) || key === "title") continue;
    const flat = isScalar(value) ? { key, value } : singleScalarEntry(value);
    if (flat && isMoney(flat.key, flat.value)) {
      return { key, label: humanizeKey(key), value: flat.value };
    }
  }
  return null;
}

function cardEntries(card: Record<string, unknown> | null, exclude: Set<string>) {
  if (!card) return [];
  return Object.entries(card)
    .filter(([key]) => !key.startsWith("_") && !HEADER_KEYS.has(key) && !exclude.has(key))
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
          const card = approval.card ?? {};
          const operationType = typeof card.operationType === "string" ? card.operationType : null;
          const title = typeof card.title === "string" ? card.title : null;
          const heading = title || approval.toolName || "approval";
          const money = findMoneyHeadline(card);
          const exclude = new Set<string>(["title", ...(money ? [money.key] : [])]);
          const bodyEntries = cardEntries(approval.card, exclude);
          const result = approval.status === "consumed" ? approval.result : null;
          const resultMessage =
            result && typeof result.message === "string" ? result.message : null;
          const resultEntries = result
            ? Object.entries(result).filter(
                ([key]) => !key.startsWith("_") && key !== "approvalId" && key !== "message",
              )
            : [];
          return (
            <article
              className="approval-card"
              key={approval.approvalId}
              data-approval-id={approval.approvalId}
            >
              <header className="approval-card-header">
                <div>
                  {operationType ? <span className="approval-op">{operationType}</span> : null}
                  <span className="approval-tool">{heading}</span>
                  <span className="approval-sub">
                    <span className="approval-id" title={approval.approvalId}>
                      {approval.approvalId}
                    </span>
                  </span>
                </div>
                <span className={`status-pill status-${approval.status}`}>{approval.status}</span>
              </header>

              {money ? (
                <div className="approval-figure">
                  <span className="approval-figure-label">{money.label}</span>
                  <span className="approval-figure-value">{formatMoney(money.value)}</span>
                </div>
              ) : null}

              {bodyEntries.length > 0 ? (
                <dl className="kv-list">
                  {bodyEntries.map(([key, value]) =>
                    renderTopEntry(humanizeKey(key), key, value, isPending),
                  )}
                </dl>
              ) : null}

              {result ? (
                <details className="kv-section result-section">
                  <summary>
                    <span className="kv-section-label">Result</span>
                    <span className="result-message">
                      {resultMessage ?? `${resultEntries.length} fields`}
                    </span>
                  </summary>
                  {resultEntries.length > 0 ? (
                    <dl className="kv-list kv-nested">
                      {resultEntries.map(([key, value]) =>
                        renderValue(humanizeKey(key), key, value, 1),
                      )}
                    </dl>
                  ) : null}
                </details>
              ) : null}
              {approval.status === "rejected" && approval.reason ? (
                <p className="state-note">
                  <span className="state-note-label">Rejection reason</span>
                  {approval.reason}
                </p>
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

              {isPending ? (
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
                    disabled={isBusy}
                  />
                  <button
                    className="icon-button danger"
                    type="button"
                    onClick={() => onReject(approval.approvalId, reason.trim() || undefined)}
                    disabled={isBusy}
                    title="Reject"
                  >
                    <X size={16} aria-hidden="true" />
                    <span className="sr-only">Reject</span>
                  </button>
                  <button
                    className="icon-button success"
                    type="button"
                    onClick={() => onApprove(approval.approvalId)}
                    disabled={isBusy}
                    title="Approve"
                  >
                    <Check size={16} aria-hidden="true" />
                    <span className="sr-only">Approve</span>
                  </button>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
