import { FormEvent, useState } from "react";
import { Send, Wrench } from "lucide-react";
import type { ThreadMessage } from "../types";

interface ConversationViewProps {
  messages: ThreadMessage[];
  provisionalAnswer: string | null;
  activeTool: string | null;
  composerDisabled: boolean;
  busyNote: string | null;
  error: string | null;
  onSend: (message: string) => Promise<void> | void;
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

export function ConversationView({
  messages,
  provisionalAnswer,
  activeTool,
  composerDisabled,
  busyNote,
  error,
  onSend,
}: ConversationViewProps) {
  const [draft, setDraft] = useState("");

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
        {activeTool ? (
          <div className="tool-chip" title="Active tool">
            <Wrench size={15} aria-hidden="true" />
            <span>{activeTool}</span>
          </div>
        ) : null}
      </div>

      <div className="message-list">
        {messages.length === 0 && !provisionalAnswer ? (
          <p className="empty-note">No messages</p>
        ) : null}
        {messages.map((message) => (
          <article className={messageClass(message.type)} key={`${message.seq}-${message.message_id}`}>
            <header>
              <span>{LABELS[message.type]}</span>
              {message.status ? <span className={`status-pill status-${message.status}`}>{message.status}</span> : null}
            </header>
            <p>{message.content}</p>
          </article>
        ))}
        {provisionalAnswer ? (
          <article className="message message-agent message-provisional">
            <header>
              <span>Agent</span>
              <span className="status-pill">streaming</span>
            </header>
            <p>{provisionalAnswer}</p>
          </article>
        ) : null}
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
