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
        {alerts.map((alert) => {
          const measure = alertMeasure(alert);
          return (
            <article className={`alert-card alert-${alert.severity}`} key={alert.alert_id}>
              <header className="alert-card-header">
                <div>
                  <p className="alert-meta">
                    {friendlyCheckName(alert.check_name)} · {alert.severity}
                  </p>
                  <h3>{alert.title}</h3>
                </div>
                <span className={`status-pill status-${alert.status}`}>{alert.status}</span>
              </header>
              <div className="alert-measure">
                <span>{measure.label}</span>
                <strong>{measure.value}</strong>
                <small>{measure.thresholdLabel}</small>
              </div>
              {alert.cause ? <p className="alert-cause">{alert.cause}</p> : null}
              <div className="alert-grounding">
                <ConfidenceBadge authority={alert.grounding.authority} />
                {diagnosticLabel(alert.grounding.diagnostic) ? (
                  <span>{diagnosticLabel(alert.grounding.diagnostic)}</span>
                ) : null}
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
          );
        })}
      </div>
    </section>
  );
}

function AlertSources({ alert }: { alert: Alert }) {
  const sources = visibleSources(alert);
  if (sources.length === 0) return null;
  return (
    <details className="alert-sources">
      <summary>Sources ({sources.length})</summary>
      <div className="alert-source-list">
        {sources.map((source) => (
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

function friendlyCheckName(checkName: string): string {
  if (checkName === "sales_drop_wow") return "Sales drop WoW";
  if (checkName === "stale_order") return "Stale order";
  if (checkName === "low_stock") return "Low stock";
  return checkName.replaceAll("_", " ");
}

function alertMeasure(alert: Alert): {
  label: string;
  value: string;
  thresholdLabel: string;
} {
  if (alert.metric === "stale_order_age_hours") {
    return {
      label: "Age",
      value: formatHours(alert.value),
      thresholdLabel: `Threshold ${formatHours(alert.threshold)}`,
    };
  }
  if (alert.metric === "sales_drop_wow") {
    return {
      label: "Week-over-week drop",
      value: formatPercent(alert.value),
      thresholdLabel: `Threshold ${formatPercent(alert.threshold)}`,
    };
  }
  if (alert.metric === "inventory") {
    return {
      label: "Inventory",
      value: formatValue(alert.value),
      thresholdLabel: `Threshold ${formatValue(alert.threshold)}`,
    };
  }
  return {
    label: alert.metric.replaceAll("_", " "),
    value: formatValue(alert.value),
    thresholdLabel: `Threshold ${formatValue(alert.threshold)}`,
  };
}

function formatPercent(value: number | string | null): string {
  if (value === null) return "n/a";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 1,
    style: "percent",
  }).format(numeric);
}

function formatHours(value: number | string | null): string {
  if (value === null) return "n/a";
  const hours = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(hours)) return String(value);
  if (hours < 24) {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(hours)} h`;
  }
  const days = Math.floor(hours / 24);
  const remainingHours = Math.round(hours % 24);
  if (remainingHours === 0) return `${days} d`;
  return `${days} d ${remainingHours} h`;
}

function diagnosticLabel(diagnostic: string | null): string | null {
  if (!diagnostic) return null;
  if (diagnostic.startsWith("cause_error:")) return null;
  return diagnostic;
}

function visibleSources(alert: Alert) {
  if (alert.grounding.diagnostic?.startsWith("cause_error:")) {
    return alert.grounding.sources.filter((source) => !source.source_id.startsWith("cause:"));
  }
  return alert.grounding.sources;
}
