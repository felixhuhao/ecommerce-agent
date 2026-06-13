import { Search } from "lucide-react";
import type { Grounding, TraceTimeline } from "../types";

interface SourcesExpanderProps {
  grounding: Grounding;
  turnId: string | null;
  timeline: TraceTimeline | undefined;
  inspectedTurnId: string | null;
  onInspect?: (turnId: string) => void;
}

function evidenceFor(timeline: TraceTimeline | undefined, spanId: string) {
  return timeline?.spans.find((span) => span.span_id === spanId)?.evidence ?? null;
}

function focusTraceSpan(spanId: string) {
  const escaped = window.CSS?.escape ? window.CSS.escape(spanId) : spanId.replace(/"/g, '\\"');
  window.requestAnimationFrame(() => {
    document
      .querySelector(`[data-trace-span-id="${escaped}"]`)
      ?.scrollIntoView({ block: "center" });
  });
}

export function SourcesExpander({
  grounding,
  turnId,
  timeline,
  inspectedTurnId,
  onInspect,
}: SourcesExpanderProps) {
  if (grounding.sources.length === 0) return null;

  const traceIsOpen = turnId !== null && inspectedTurnId === turnId;

  return (
    <details className="sources-expander">
      <summary>Sources ({grounding.sources.length})</summary>
      <div className="sources-list">
        {grounding.sources.map((source) => {
          const evidence = traceIsOpen ? evidenceFor(timeline, source.span_id) : null;
          return (
            <div className="source-row" key={source.span_id}>
              <div className="source-row-head">
                <span>{source.tool_name}</span>
                {turnId && onInspect ? (
                  <button
                    className="source-trace-button"
                    onClick={() => {
                      onInspect(turnId);
                      focusTraceSpan(source.span_id);
                    }}
                    type="button"
                  >
                    <Search size={13} aria-hidden="true" /> Trace
                  </button>
                ) : null}
              </div>
              {source.args_summary ? (
                <p>
                  <span>args</span> {source.args_summary}
                </p>
              ) : null}
              {source.result_summary ? (
                <p>
                  <span>result</span> {source.result_summary}
                </p>
              ) : null}
              {evidence ? (
                <p>
                  <span>evidence</span> {evidence}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </details>
  );
}
