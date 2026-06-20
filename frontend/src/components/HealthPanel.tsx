import { Activity, Box, Database, Server } from "lucide-react";
import type { HealthStatus, McpHealth } from "../types";

interface HealthPanelProps {
  health?: HealthStatus;
  mcp?: McpHealth;
  healthUnavailable?: boolean;
  mcpUnavailable?: boolean;
}

const MCP_SERVER_LABELS: Record<string, string> = {
  spring: "Commerce tools",
};

function mcpServerLabel(name: string): string {
  return MCP_SERVER_LABELS[name] ?? name;
}

function mcpServerDetail(name: string, toolCount?: number, error?: string): string | undefined {
  if (error) return error;
  const tools = typeof toolCount === "number" ? `${toolCount} tools` : undefined;
  return tools ? `${tools} · ${name}` : name;
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

export function HealthPanel({
  health,
  mcp,
  healthUnavailable = false,
  mcpUnavailable = false,
}: HealthPanelProps) {
  const components = health?.components ?? {};
  const showCoreUnavailable = healthUnavailable && !health;
  const showMcpUnavailable = mcpUnavailable && !mcp;

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
          <span>Workspace</span>
        </div>
        {showCoreUnavailable ? (
          <StatusRow label="API" status="unavailable" detail="health endpoint unavailable" />
        ) : (
          <>
            <StatusRow
              label="Conversation store"
              status={components.mongo?.status}
              detail={components.mongo?.error}
            />
            <StatusRow
              label="Analysis sandbox"
              status={components.sandbox?.status}
              detail={components.sandbox?.error}
            />
            <StatusRow
              label="AI model"
              status={components.model?.status}
              detail={components.model?.model ?? components.model?.checked}
            />
          </>
        )}
      </div>

      <div className="health-group">
        <div className="health-group-title">
          <Server size={15} aria-hidden="true" />
          <span>Tool Providers</span>
        </div>
        {showMcpUnavailable ? (
          <StatusRow
            label="Tool gateway"
            status="unavailable"
            detail="health/mcp endpoint unavailable"
          />
        ) : mcp?.servers
          ? Object.entries(mcp.servers).map(([name, server]) => (
              <StatusRow
                key={name}
                label={mcpServerLabel(name)}
                status={server.status}
                detail={mcpServerDetail(name, server.tool_count, server.error)}
              />
            ))
          : null}
        {!mcp?.servers && !showMcpUnavailable ? <StatusRow label="Tool gateway" status="unknown" /> : null}
      </div>

      <div className="health-group-title muted">
        <Box size={15} aria-hidden="true" />
        <span>
          {health?.environment
            ? `Environment: ${health.environment}`
            : healthUnavailable
              ? "API unavailable"
              : "Environment"}
        </span>
      </div>
    </section>
  );
}
