import { Cpu, Download, Wrench } from "lucide-react";
import type { TraceSpan, TraceTimeline } from "../types";

interface TracePanelProps {
  timeline: TraceTimeline | undefined;
  inspectedTurnId: string | null;
  isLoading: boolean;
  isError: boolean;
  exportHref: string | null;
  onViewApproval: (approvalId: string) => void;
}

function statusClass(status: string) {
  if (status === "ok") return "ok";
  if (status === "pending") return "warn";
  return "bad";
}

function SpanRow({
  span,
  onViewApproval,
}: {
  span: TraceSpan;
  onViewApproval: (approvalId: string) => void;
}) {
  const Icon = span.kind === "tool_call" ? Wrench : Cpu;
  return (
    <details className="trace-span" data-trace-span-id={span.span_id}>
      <summary>
        <Icon size={14} aria-hidden="true" />
        <span className="trace-span-name">{span.name ?? span.kind}</span>
        <span className={`status-dot ${statusClass(span.status)}`} aria-hidden="true" />
        {span.duration_ms != null ? (
          <span className="trace-span-dur">{Math.round(span.duration_ms)} ms</span>
        ) : null}
      </summary>
      <div className="trace-span-body">
        {span.args_summary ? (
          <p>
            <span className="label">args</span> {span.args_summary}
          </p>
        ) : null}
        {span.result_summary ? (
          <p>
            <span className="label">result</span> {span.result_summary}
          </p>
        ) : null}
        {span.evidence ? (
          <p>
            <span className="label">evidence</span> {span.evidence}
          </p>
        ) : null}
        {span.tokens_in != null || span.tokens_out != null ? (
          <p className="trace-tokens">
            tokens {span.tokens_in ?? 0} in / {span.tokens_out ?? 0} out
          </p>
        ) : null}
        {span.error_message ? <p className="trace-error">{span.error_message}</p> : null}
        {span.artifact_id ? (
          <p>
            <span className="label">artifact</span> {span.artifact_id} (shown in the message)
          </p>
        ) : null}
        {span.approval_id ? (
          <button
            type="button"
            className="trace-link"
            onClick={() => onViewApproval(span.approval_id as string)}
          >
            View approval
          </button>
        ) : null}
      </div>
    </details>
  );
}

export function TracePanel({
  timeline,
  inspectedTurnId,
  isLoading,
  isError,
  exportHref,
  onViewApproval,
}: TracePanelProps) {
  return (
    <section className="rail-panel trace-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Provenance</p>
          <h2>Trace</h2>
        </div>
        {timeline && exportHref ? (
          <a className="trace-export" href={exportHref} download>
            <Download size={14} aria-hidden="true" /> JSON
          </a>
        ) : null}
      </div>
      {!inspectedTurnId ? (
        <p className="empty-note">Select an answer's Inspect to view its trace</p>
      ) : isError ? (
        <p className="notice notice-error">Could not load trace.</p>
      ) : isLoading || !timeline ? (
        <p className="empty-note">Loading trace...</p>
      ) : (
        <>
          <div className="trace-summary">
            <span>{timeline.span_count} spans</span>
            {timeline.duration_ms != null ? (
              <span>{Math.round(timeline.duration_ms)} ms</span>
            ) : null}
            {timeline.tokens_in_total != null || timeline.tokens_out_total != null ? (
              <span>
                {timeline.tokens_in_total ?? 0}/{timeline.tokens_out_total ?? 0} tok
              </span>
            ) : null}
          </div>
          {timeline.spans.length === 0 ? (
            <p className="empty-note">No tool or model activity recorded.</p>
          ) : (
            <div className="trace-timeline">
              {timeline.spans.map((span) => (
                <SpanRow
                  key={span.span_id}
                  span={span}
                  onViewApproval={onViewApproval}
                />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
