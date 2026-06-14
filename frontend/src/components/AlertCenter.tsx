import type { Alert } from "../types";
import { ConfidenceBadge } from "./ConfidenceBadge";

interface AlertCenterProps {
  alerts: Alert[];
  isLoading: boolean;
  isError: boolean;
  isRunning: boolean;
  isAcknowledgingId: string | null;
  actionError: string | null;
  runNote: string | null;
  onRun: () => void;
  onAcknowledge: (alertId: string) => void;
}

export function AlertCenter({
  alerts,
  isLoading,
  isError,
  isRunning,
  isAcknowledgingId,
  actionError,
  runNote,
  onRun,
  onAcknowledge,
}: AlertCenterProps) {
  return (
    <section className="alert-center" aria-label="Alert Center">
      <div className="rail-panel-head">
        <div>
          <p className="rail-kicker">Alert Center</p>
          <h2>Monitor alerts</h2>
        </div>
        <button className="rail-action-button" disabled={isRunning} type="button" onClick={onRun}>
          {isRunning ? "Running" : "Run"}
        </button>
      </div>
      {actionError ? <p className="panel-error">{actionError}</p> : null}
      {runNote ? <p className="panel-note">{runNote}</p> : null}
      {isLoading ? <p className="empty-panel">Loading alerts</p> : null}
      {isError ? <p className="panel-error">Alerts unavailable.</p> : null}
      {!isLoading && !isError && alerts.length === 0 ? (
        <p className="empty-panel">No alerts.</p>
      ) : null}
      <div className="alert-list">
        {alerts.map((alert) => (
          <article className={`alert-card alert-${alert.severity}`} key={alert.alert_id}>
            <header className="alert-card-header">
              <div>
                <p className="alert-meta">
                  {alert.check_name.replaceAll("_", " ")} · {alert.severity}
                </p>
                <h3>{alert.title}</h3>
              </div>
              <span className={`status-pill status-${alert.status}`}>{alert.status}</span>
            </header>
            <div className="alert-measure">
              <span>{alert.metric}</span>
              <strong>{formatValue(alert.value)}</strong>
              <small>threshold {formatValue(alert.threshold)}</small>
            </div>
            {alert.cause ? <p className="alert-cause">{alert.cause}</p> : null}
            <div className="alert-grounding">
              <ConfidenceBadge authority={alert.grounding.authority} />
              {alert.grounding.diagnostic ? <span>{alert.grounding.diagnostic}</span> : null}
            </div>
            <AlertSources alert={alert} />
            {alert.status === "open" ? (
              <button
                className="rail-action-button alert-ack"
                disabled={isAcknowledgingId === alert.alert_id}
                type="button"
                onClick={() => onAcknowledge(alert.alert_id)}
              >
                {isAcknowledgingId === alert.alert_id ? "Acknowledging" : "Acknowledge"}
              </button>
            ) : (
              <p className="alert-ack-note">
                Acknowledged{alert.acknowledged_by ? ` by ${alert.acknowledged_by}` : ""}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function AlertSources({ alert }: { alert: Alert }) {
  if (alert.grounding.sources.length === 0) return null;
  return (
    <details className="alert-sources">
      <summary>Sources ({alert.grounding.sources.length})</summary>
      <div className="alert-source-list">
        {alert.grounding.sources.map((source) => (
          <div className="alert-source-row" key={source.source_id}>
            <div className="alert-source-head">
              <strong>{source.tool_name}</strong>
              <span>{source.source_id}</span>
            </div>
            {source.args_summary ? <p>{source.args_summary}</p> : null}
            {source.result_summary ? <p>{source.result_summary}</p> : null}
            {source.evidence ? <pre>{source.evidence}</pre> : null}
          </div>
        ))}
      </div>
    </details>
  );
}

function formatValue(value: number | string | null): string {
  if (value === null) return "n/a";
  return typeof value === "number" ? new Intl.NumberFormat().format(value) : value;
}

