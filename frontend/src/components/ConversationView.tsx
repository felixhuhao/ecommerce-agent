import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Check, Download, RefreshCw, Send, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { StreamStatus } from "../api/useSessionStream";
import { extFromMime } from "../lib/mime";
import { foldApprovals } from "../state/approvals";
import type { ThreadMessage, TurnProgressStep } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { SourcesExpander } from "./SourcesExpander";
import { TurnStatusTracker } from "./TurnStatusTracker";
import {
  EChartsArtifact,
  type EChartsArtifactSpec,
  UnsupportedChartArtifact,
  isEChartsArtifact,
  isValidEChartsArtifact,
} from "./EChartsArtifact";

interface ConversationViewProps {
  messages: ThreadMessage[];
  provisionalAnswer: string | null;
  streamStatus: StreamStatus;
  composerDisabled: boolean;
  busyNote: string | null;
  error: string | null;
  onSend: (message: string) => Promise<void> | void;
  onApprove?: (approvalId: string) => Promise<void> | void;
  onReject?: (approvalId: string, reason: string | undefined) => Promise<void> | void;
  pendingApprovalId?: string | null;
  turnProgress?: TurnProgressStep[];
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
  mimeType: string;
  toolName: string | null;
}

type RenderableArtifact =
  | { kind: "image"; artifact: ImageArtifact }
  | { kind: "echarts"; artifact: EChartsArtifactSpec }
  | { kind: "unsupported"; id: string };

function imageArtifact(item: unknown, index: number): ImageArtifact | null {
  if (!item || typeof item !== "object") return null;
  const artifact = item as Record<string, unknown>;
  const src = artifact.src;
  if (typeof src !== "string" || !src.startsWith("data:image/")) return null;
  const id = artifact.id;
  const title = artifact.title;
  const mimeType = artifact.mime_type;
  const toolName = artifact.tool_name;
  return {
    id: typeof id === "string" && id.length > 0 ? id : `chart-${index}`,
    src,
    title: typeof title === "string" && title.length > 0 ? title : "Generated chart",
    mimeType: typeof mimeType === "string" && mimeType.length > 0 ? mimeType : "image/png",
    toolName: typeof toolName === "string" ? toolName : null,
  };
}

function renderableArtifacts(result: Record<string, unknown> | null): RenderableArtifact[] {
  const artifacts = result?.artifacts;
  if (!Array.isArray(artifacts)) return [];

  return artifacts.flatMap((item, index): RenderableArtifact[] => {
    if (isValidEChartsArtifact(item)) {
      return [{ kind: "echarts", artifact: item }];
    }
    const image = imageArtifact(item, index);
    if (image) {
      return [{ kind: "image", artifact: image }];
    }
    if (isEChartsArtifact(item) || (item && typeof item === "object")) {
      const id = (item as Record<string, unknown>).id;
      return [{ kind: "unsupported", id: typeof id === "string" ? id : `artifact-${index}` }];
    }
    return [];
  });
}

interface ApprovalState {
  status: string | null;
  reason: string | null;
}

function approvalStates(messages: ThreadMessage[]): Map<string, ApprovalState> {
  const states = new Map<string, ApprovalState>();
  for (const approval of foldApprovals(messages)) {
    states.set(approval.approvalId, {
      status: approval.status,
      reason: approval.reason,
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
  const approvalId = message.approval_id;
  const isBusy = pendingApprovalId === approvalId;

  return (
    <section className="inline-approval" aria-label="Approval card">
      <div className="inline-approval-head">
        <div>
          <span className="inline-approval-kicker">Approval card</span>
          <strong>{inlineCardTitle(message)}</strong>
          <code>{approvalId}</code>
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
            aria-label={`Reject reason for ${approvalId}`}
            value={reason}
            onChange={(event) => setReason(event.currentTarget.value)}
            disabled={isBusy}
          />
          <button
            className="icon-button danger"
            type="button"
            onClick={() => onReject(approvalId, reason.trim() || undefined)}
            disabled={isBusy}
            title="Reject"
          >
            <X size={17} aria-hidden="true" />
            <span className="sr-only">Reject</span>
          </button>
          <button
            className="icon-button success"
            type="button"
            onClick={() => onApprove(approvalId)}
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
  streamStatus,
  composerDisabled,
  busyNote,
  error,
  onSend,
  onApprove,
  onReject,
  pendingApprovalId = null,
  turnProgress = [],
}: ConversationViewProps) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const approvals = useMemo(() => approvalStates(messages), [messages]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, provisionalAnswer, busyNote, error]);

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
        </div>
      </div>

      <div className="message-list">
        {messages.length === 0 && !provisionalAnswer ? (
          <p className="empty-note">No messages</p>
        ) : null}
        {messages.map((message) => {
          const artifacts = renderableArtifacts(message.result);
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
                />
              ) : null}
              {artifacts.length > 0 ? (
                <div className="message-artifacts">
                  {artifacts.map((item) => {
                    if (item.kind === "echarts") {
                      return <EChartsArtifact artifact={item.artifact} key={item.artifact.id} />;
                    }
                    if (item.kind === "unsupported") {
                      return <UnsupportedChartArtifact key={item.id} />;
                    }
                    const artifact = item.artifact;
                    return (
                      <figure className="chart-artifact" key={artifact.id}>
                        <img src={artifact.src} alt={artifact.title} />
                        <figcaption>
                          <span className="chart-artifact-title">{artifact.title}</span>
                          <span className="chart-artifact-meta">
                            {artifact.toolName ? <span>{artifact.toolName}</span> : null}
                            <a
                              className="artifact-download"
                              href={artifact.src}
                              download={`${artifact.id}.${extFromMime(artifact.mimeType)}`}
                            >
                              <Download size={14} aria-hidden="true" /> Download
                            </a>
                          </span>
                        </figcaption>
                      </figure>
                    );
                  })}
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

      <TurnStatusTracker steps={turnProgress} />

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
