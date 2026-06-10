import { Plus } from "lucide-react";
import type { SessionSummary } from "../types";

interface SessionSidebarProps {
  sessions: SessionSummary[];
  activeId: string | null;
  isCreating?: boolean;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
}

function compactDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function SessionSidebar({
  sessions,
  activeId,
  isCreating = false,
  onSelect,
  onNew,
}: SessionSidebarProps) {
  return (
    <div className="sidebar-panel">
      <div className="pane-header">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>Sessions</h1>
        </div>
        <button className="icon-button" type="button" onClick={onNew} disabled={isCreating} title="New session">
          <Plus size={18} aria-hidden="true" />
          <span className="sr-only">New session</span>
        </button>
      </div>

      <div className="session-list">
        {sessions.length === 0 ? (
          <p className="empty-note">No sessions</p>
        ) : (
          sessions.map((session) => {
            const title = session.title || "Untitled session";
            return (
              <button
                className={`session-row ${session.session_id === activeId ? "is-active" : ""}`}
                key={session.session_id}
                type="button"
                onClick={() => onSelect(session.session_id)}
              >
                <span className="session-row-title">{title}</span>
                <span className="session-row-meta">
                  {session.message_count} msg · {compactDate(session.created_at)}
                </span>
                {session.last_message_preview ? (
                  <span className="session-row-preview">{session.last_message_preview}</span>
                ) : null}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
