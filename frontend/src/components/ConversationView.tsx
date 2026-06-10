import { FormEvent, useEffect, useRef, useState } from "react";
import { Activity, RefreshCw, Send, Wrench } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { StreamStatus } from "../api/useSessionStream";
import type { ThreadMessage } from "../types";

interface ConversationViewProps {
  messages: ThreadMessage[];
  provisionalAnswer: string | null;
  activeTool: string | null;
  streamStatus: StreamStatus;
  composerDisabled: boolean;
  busyNote: string | null;
  error: string | null;
  onSend: (message: string) => Promise<void> | void;
  onInspect?: (turnId: string) => void;
  focusMessageId?: string | null;
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

export function ConversationView({
  messages,
  provisionalAnswer,
  activeTool,
  streamStatus,
  composerDisabled,
  busyNote,
  error,
  onSend,
  onInspect,
  focusMessageId,
}: ConversationViewProps) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, provisionalAnswer, activeTool, busyNote, error]);

  useEffect(() => {
    if (!focusMessageId) return;
    document.querySelector(`[data-message-id="${focusMessageId}"]`)?.scrollIntoView({ block: "center" });
  }, [focusMessageId]);

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
          return (
            <article
              className={messageClass(message.type)}
              key={`${message.seq}-${message.message_id}`}
              data-message-id={message.message_id}
            >
              <header>
                <span>{LABELS[message.type]}</span>
                {message.status ? <span className={`status-pill status-${message.status}`}>{message.status}</span> : null}
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
