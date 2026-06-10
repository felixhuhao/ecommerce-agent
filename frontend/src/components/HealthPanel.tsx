import { Activity, Box, Database, Server } from "lucide-react";
import type { HealthStatus, McpHealth } from "../types";

interface HealthPanelProps {
  health?: HealthStatus;
  mcp?: McpHealth;
}

function statusClass(status?: string) {
  if (status === "ok") return "ok";
  if (status === "unconfigured") return "warn";
  return "bad";
}

function StatusRow({
  label,
  status,
  detail,
}: {
  label: string;
  status?: string;
  detail?: string | number;
}) {
  return (
    <div className="health-row">
      <span className={`status-dot ${statusClass(status)}`} />
      <span>{label}</span>
      <strong>{status || "unknown"}</strong>
      {detail ? <small>{detail}</small> : null}
    </div>
  );
}

export function HealthPanel({ health, mcp }: HealthPanelProps) {
  const components = health?.components ?? {};

  return (
    <section className="rail-panel health-panel">
      <div className="pane-header compact">
        <div>
          <p className="eyebrow">Runtime</p>
          <h2>System</h2>
        </div>
        <Activity size={18} aria-hidden="true" />
      </div>

      <div className="health-group">
        <div className="health-group-title">
          <Database size={15} aria-hidden="true" />
          <span>Core</span>
        </div>
        <StatusRow label="Mongo" status={components.mongo?.status} detail={components.mongo?.error} />
        <StatusRow label="Sandbox" status={components.sandbox?.status} detail={components.sandbox?.error} />
        <StatusRow
          label="Model"
          status={components.model?.status}
          detail={components.model?.model ?? components.model?.checked}
        />
      </div>

      <div className="health-group">
        <div className="health-group-title">
          <Server size={15} aria-hidden="true" />
          <span>MCP</span>
        </div>
        {mcp?.servers
          ? Object.entries(mcp.servers).map(([name, server]) => (
              <StatusRow
                key={name}
                label={name}
                status={server.status}
                detail={typeof server.tool_count === "number" ? `${server.tool_count} tools` : server.error}
              />
            ))
          : null}
        {!mcp?.servers ? <StatusRow label="MCP" status="unknown" /> : null}
      </div>

      <div className="health-group-title muted">
        <Box size={15} aria-hidden="true" />
        <span>{health?.environment || "environment"}</span>
      </div>
    </section>
  );
}
