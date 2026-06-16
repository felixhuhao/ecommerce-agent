import type { Grounding } from "../types";

interface SourcesExpanderProps {
  grounding: Grounding;
}

export function SourcesExpander({ grounding }: SourcesExpanderProps) {
  if (grounding.sources.length === 0) return null;

  return (
    <details className="sources-expander">
      <summary>Sources ({grounding.sources.length})</summary>
      <div className="sources-list">
        {grounding.sources.map((source) => {
          return (
            <div className="source-row" key={source.span_id}>
              <div className="source-row-head">
                <span>{source.tool_name}</span>
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
            </div>
          );
        })}
      </div>
    </details>
  );
}
