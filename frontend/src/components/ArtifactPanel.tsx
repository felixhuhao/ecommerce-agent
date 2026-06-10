import { Download } from "lucide-react";
import { formatRelativeTime } from "../lib/datetime";
import { extFromMime } from "../lib/mime";
import type { ArtifactSummary } from "../types";

interface ArtifactPanelProps {
  artifacts: ArtifactSummary[];
  isLoading: boolean;
  isError: boolean;
  onJumpToMessage: (messageId: string) => void;
}

export function ArtifactPanel({ artifacts, isLoading, isError, onJumpToMessage }: ArtifactPanelProps) {
  return (
    <section className="rail-panel artifact-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Outputs</p>
          <h2>Artifacts</h2>
        </div>
      </div>
      {isError ? (
        <p className="notice notice-error">Could not load artifacts.</p>
      ) : isLoading ? (
        <p className="empty-note">Loading artifacts...</p>
      ) : artifacts.length === 0 ? (
        <p className="empty-note">No charts generated in this session yet</p>
      ) : (
        <div className="artifact-grid">
          {artifacts.map((artifact) => (
            <figure className="artifact-card" key={`${artifact.message_id}:${artifact.id}`}>
              <img src={artifact.src} alt={artifact.tool_name ?? "chart"} />
              <figcaption>
                <span className="artifact-tool">{artifact.tool_name ?? "chart"}</span>
                <span className="artifact-time">{formatRelativeTime(artifact.created_at)}</span>
              </figcaption>
              <div className="artifact-actions">
                <a
                  className="artifact-download"
                  href={artifact.src}
                  download={`${artifact.id}.${extFromMime(artifact.mime_type)}`}
                >
                  <Download size={14} aria-hidden="true" /> Download
                </a>
                <button type="button" onClick={() => onJumpToMessage(artifact.message_id)}>
                  Jump to message
                </button>
              </div>
            </figure>
          ))}
        </div>
      )}
    </section>
  );
}
