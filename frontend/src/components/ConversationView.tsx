import { FormEvent, useEffect, useRef, useState } from "react";
import { Activity, Check, RefreshCw, Send, Wrench, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { StreamStatus } from "../api/useSessionStream";
import type { ThreadMessage, TraceTimeline } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { SourcesExpander } from "./SourcesExpander";

interface ConversationViewProps {
  messages: ThreadMessage[];
  provisionalAnswer: string | null;
  activeTool: string | null;
  streamStatus: StreamStatus;
  composerDisabled: boolean;
  busyNote: string | null;
  error: string | null;
  onSend: (message: string) => Promise<void> | void;
  onApprove?: (approvalId: string) => Promise<void> | void;
  onReject?: (approvalId: string, reason: string | undefined) => Promise<void> | void;
  pendingApprovalId?: string | null;
  onInspect?: (turnId: string) => void;
  traceTimeline?: TraceTimeline;
  inspectedTurnId?: string | null;
  focusMessageId?: string | null;
  onFocusMessageHandled?: () => void;
}

const LABELS: Record<ThreadMessage["type"], string> = {
  user: "Operator",
  agent_answer: "Agent",
  agent_proposal: "Proposal",
  approval_status: "Approval",
  execution_result: "Execution",
};

function messageClass(type: ThreadMessage["type"]) {
  if (type === "user") return "message message-user";
  if (type === "agent_proposal") return "message message-proposal";
  if (type === "approval_status" || type === "execution_result") return "message message-system";
  return "message message-agent";
}

// Operators type plain text; everything the agent/backend writes is GitHub-flavored markdown.
function MessageBody({ type, content }: { type: ThreadMessage["type"]; content: string }) {
  if (type === "user") {
    return <p className="message-text">{content}</p>;
  }
  return (
    <div className="message-md">
      <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
    </div>
  );
}

interface ImageArtifact {
  id: string;
  src: string;
  title: string;
  toolName: string | null;
}

function imageArtifacts(result: Record<string, unknown> | null): ImageArtifact[] {
  const artifacts = result?.artifacts;
  if (!Array.isArray(artifacts)) return [];

  return artifacts.flatMap((item, index) => {
    if (!item || typeof item !== "object") return [];
    const artifact = item as Record<string, unknown>;
    const src = artifact.src;
    if (typeof src !== "string" || !src.startsWith("data:image/")) return [];
    const id = artifact.id;
    const title = artifact.title;
    const toolName = artifact.tool_name;
    return [
      {
        id: typeof id === "string" && id.length > 0 ? id : `chart-${index}`,
        src,
        title: typeof title === "string" && title.length > 0 ? title : "Generated chart",
        toolName: typeof toolName === "string" ? toolName : null,
      },
    ];
  });
}

interface ApprovalState {
  status: string | null;
  reason: string | null;
}

function approvalStates(messages: ThreadMessage[]): Map<string, ApprovalState> {
  const states = new Map<string, ApprovalState>();
  for (const message of messages) {
    if (!message.approval_id) continue;
    const current = states.get(message.approval_id) ?? { status: null, reason: null };
    states.set(message.approval_id, {
      status: message.status ?? current.status,
      reason: message.reason ?? current.reason,
    });
  }
  return states;
}

function inlineCardTitle(message: ThreadMessage) {
  const title = message.card?.title;
  if (typeof title === "string" && title.trim()) return title;
  return message.tool_name ?? "Approval";
}

function headerStatus(message: ThreadMessage) {
  if (message.type === "agent_proposal" && message.approval_id && message.card) {
    return null;
  }
  return message.status;
}

function InlineApprovalCard({
  message,
  state,
  pendingApprovalId,
  onApprove,
  onReject,
}: {
  message: ThreadMessage;
  state: ApprovalState | null;
  pendingApprovalId: string | null;
  onApprove?: (approvalId: string) => Promise<void> | void;
  onReject?: (approvalId: string, reason: string | undefined) => Promise<void> | void;
}) {
  const [reason, setReason] = useState("");
  if (message.type !== "agent_proposal" || !message.approval_id || !message.card) {
    return null;
  }

  const status = state?.status ?? message.status ?? "pending";
  const isPending = status === "pending";
  const isBusy = pendingApprovalId === message.approval_id;

  return (
    <section className="inline-approval" aria-label="Approval card">
      <div className="inline-approval-head">
        <div>
          <span className="inline-approval-kicker">Approval card</span>
          <strong>{inlineCardTitle(message)}</strong>
          <code>{message.approval_id}</code>
        </div>
        <span className={`status-pill status-${status}`}>{status}</span>
      </div>
      {state?.reason ? (
        <p className="inline-approval-note">
          <span>Reason</span>
          {state.reason}
        </p>
      ) : null}
      {isPending && onApprove && onReject ? (
        <div className="inline-approval-actions">
          <input
            aria-label={`Reject reason for ${message.approval_id}`}
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value)}
            disabled={isBusy}
          />
          <button
            className="icon-button danger"
            type="button"
            onClick={() => onReject(message.approval_id as string, reason.trim() || undefined)}
            disabled={isBusy}
            title="Reject"
          >
            <X size={17} aria-hidden="true" />
            <span className="sr-only">Reject</span>
          </button>
          <button
            className="icon-button success"
            type="button"
            onClick={() => onApprove(message.approval_id as string)}
            disabled={isBusy}
            title="Approve"
          >
            <Check size={17} aria-hidden="true" />
            <span className="sr-only">Approve</span>
          </button>
        </div>
      ) : null}
    </section>
  );
}

export function ConversationView({
  messages,
  provisionalAnswer,
  activeTool,
  streamStatus,
  composerDisabled,
  busyNote,
  error,
  onSend,
  onApprove,
  onReject,
  pendingApprovalId = null,
  onInspect,
  traceTimeline,
  inspectedTurnId = null,
  focusMessageId,
  onFocusMessageHandled,
}: ConversationViewProps) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const approvals = approvalStates(messages);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, provisionalAnswer, activeTool, busyNote, error]);

  useEffect(() => {
    if (!focusMessageId) return;
    document.querySelector(`[data-message-id="${focusMessageId}"]`)?.scrollIntoView({ block: "center" });
    onFocusMessageHandled?.();
  }, [focusMessageId, onFocusMessageHandled]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const message = draft.trim();
    if (!message || composerDisabled) return;
    setDraft("");
    await onSend(message);
  };

  return (
    <div className="conversation-panel">
      <div className="pane-header conversation-header">
        <div>
          <p className="eyebrow">Operator Console</p>
          <h2>Conversation</h2>
        </div>
        <div className="header-chips">
          {streamStatus === "reconnecting" ? (
            <div className="tool-chip stream-chip" title="Stream reconnecting">
              <RefreshCw size={15} aria-hidden="true" />
              <span>Reconnecting</span>
            </div>
          ) : null}
          {activeTool ? (
            <div className="tool-chip" title="Active tool">
              <Wrench size={15} aria-hidden="true" />
              <span>{activeTool}</span>
            </div>
          ) : null}
        </div>
      </div>

      <div className="message-list">
        {messages.length === 0 && !provisionalAnswer ? (
          <p className="empty-note">No messages</p>
        ) : null}
        {messages.map((message) => {
          const artifacts = imageArtifacts(message.result);
          const status = headerStatus(message);
          return (
            <article
              className={messageClass(message.type)}
              key={`${message.seq}-${message.message_id}`}
              data-message-id={message.message_id}
            >
              <header>
                <div className="message-header-left">
                  <span>{LABELS[message.type]}</span>
                  {message.type !== "agent_proposal" && message.grounding ? (
                    <ConfidenceBadge authority={message.grounding.authority} />
                  ) : null}
                </div>
                {status ? <span className={`status-pill status-${status}`}>{status}</span> : null}
                {onInspect && message.turn_id && (message.type === "agent_answer" || message.type === "agent_proposal") ? (
                  <button
                    type="button"
                    className="inspect-button"
                    onClick={() => onInspect(message.turn_id as string)}
                  >
                    <Activity size={13} aria-hidden="true" /> Inspect
                  </button>
                ) : null}
              </header>
              <MessageBody type={message.type} content={message.content} />
              <InlineApprovalCard
                message={message}
                state={message.approval_id ? approvals.get(message.approval_id) ?? null : null}
                pendingApprovalId={pendingApprovalId}
                onApprove={onApprove}
                onReject={onReject}
              />
              {message.grounding ? (
                <SourcesExpander
                  grounding={message.grounding}
                  inspectedTurnId={inspectedTurnId}
                  onInspect={onInspect}
                  timeline={traceTimeline}
                  turnId={message.turn_id}
                />
              ) : null}
              {artifacts.length > 0 ? (
                <div className="message-artifacts">
                  {artifacts.map((artifact) => (
                    <figure className="chart-artifact" key={artifact.id}>
                      <img src={artifact.src} alt={artifact.title} />
                      <figcaption>
                        <span>{artifact.title}</span>
                        {artifact.toolName ? <span>{artifact.toolName}</span> : null}
                      </figcaption>
                    </figure>
                  ))}
                </div>
              ) : null}
            </article>
          );
        })}
        {provisionalAnswer ? (
          <article className="message message-agent message-provisional">
            <header>
              <span>Agent</span>
              <span className="status-pill">streaming</span>
            </header>
            <p className="message-text">{provisionalAnswer}</p>
          </article>
        ) : null}
        <div ref={endRef} />
      </div>

      <div className="notice-stack">
        {busyNote ? <div className="notice notice-warn">{busyNote}</div> : null}
        {error ? <div className="notice notice-error">{error}</div> : null}
      </div>

      <form className="composer" onSubmit={submit}>
        <textarea
          aria-label="Message"
          value={draft}
          onChange={(event) => setDraft(event.currentTarget.value)}
          disabled={composerDisabled}
          rows={2}
        />
        <button
          className="primary-icon-button"
          type="submit"
          disabled={composerDisabled || draft.trim().length === 0}
          title="Send"
        >
          <Send size={18} aria-hidden="true" />
          <span className="sr-only">Send</span>
        </button>
      </form>
    </div>
  );
}
