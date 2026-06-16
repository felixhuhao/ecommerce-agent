import { Cpu, Download, Wrench } from "lucide-react";
import type { TraceSpan, TraceTimeline } from "../types";

export interface TraceTurnOption {
  turnId: string;
  label: string;
}

interface TracePanelProps {
  timeline: TraceTimeline | undefined;
  selectedTurnId: string | null;
  turnOptions: TraceTurnOption[];
  isLoading: boolean;
  isError: boolean;
  exportHref: string | null;
  onSelectTurn: (turnId: string) => void;
  onViewApproval: (approvalId: string) => void;
}

function statusClass(status: string) {
  if (status === "ok") return "ok";
  if (status === "pending") return "warn";
  return "bad";
}

function formatDuration(ms: number) {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
}

function formatCount(n: number) {
  return n.toLocaleString("en-US");
}

function SpanRow({
  span,
  maxDuration,
  onViewApproval,
}: {
  span: TraceSpan;
  maxDuration: number;
  onViewApproval: (approvalId: string) => void;
}) {
  const Icon = span.kind === "tool_call" ? Wrench : Cpu;
  // Width is proportional to this span's slice of the slowest span, so the
  // dominant model call reads at a glance. Tiny spans keep a visible sliver.
  const fraction =
    maxDuration > 0 && span.duration_ms != null
      ? Math.max(0.04, span.duration_ms / maxDuration)
      : 0;
  return (
    <details
      className="trace-span"
      data-status={statusClass(span.status)}
      data-trace-span-id={span.span_id}
    >
      <summary>
        <span className="trace-span-head">
          <Icon size={14} aria-hidden="true" />
          <span className="trace-span-name">{span.name ?? span.kind}</span>
          {span.duration_ms != null ? (
            <span className="trace-span-dur">{formatDuration(span.duration_ms)}</span>
          ) : null}
        </span>
        {fraction > 0 ? (
          <span className="trace-span-track" aria-hidden="true">
            <span className="trace-span-bar" style={{ width: `${fraction * 100}%` }} />
          </span>
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
  selectedTurnId,
  turnOptions,
  isLoading,
  isError,
  exportHref,
  onSelectTurn,
  onViewApproval,
}: TracePanelProps) {
  const maxDuration = timeline
    ? timeline.spans.reduce((max, span) => Math.max(max, span.duration_ms ?? 0), 0)
    : 0;
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
      {turnOptions.length > 0 ? (
        <label className="trace-turn-select">
          <span>Turn</span>
          <select
            aria-label="Trace turn"
            value={selectedTurnId ?? ""}
            onChange={(event) => onSelectTurn(event.currentTarget.value)}
          >
            {turnOptions.map((turn) => (
              <option key={turn.turnId} value={turn.turnId}>
                {turn.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {!selectedTurnId ? (
        <p className="empty-note">No completed turns with traces yet</p>
      ) : isError ? (
        <p className="notice notice-error">Could not load trace.</p>
      ) : isLoading || !timeline ? (
        <p className="empty-note">Loading trace...</p>
      ) : (
        <>
          <div className="trace-summary">
            <div className="trace-stat">
              <span className="trace-stat-num">{formatCount(timeline.span_count)}</span>
              <span className="trace-stat-label">
                {timeline.span_count === 1 ? "span" : "spans"}
              </span>
            </div>
            {timeline.duration_ms != null ? (
              <div className="trace-stat">
                <span className="trace-stat-num">
                  {formatDuration(timeline.duration_ms)}
                </span>
                <span className="trace-stat-label">latency</span>
              </div>
            ) : null}
            {timeline.tokens_in_total != null || timeline.tokens_out_total != null ? (
              <div className="trace-stat">
                <span className="trace-stat-num">
                  {formatCount(timeline.tokens_in_total ?? 0)}
                  <span className="trace-stat-sep">/</span>
                  {formatCount(timeline.tokens_out_total ?? 0)}
                </span>
                <span className="trace-stat-label">tokens in·out</span>
              </div>
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
                  maxDuration={maxDuration}
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
