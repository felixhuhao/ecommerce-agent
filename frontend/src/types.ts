export type MessageType =
  | "user"
  | "agent_answer"
  | "agent_proposal"
  | "approval_status"
  | "execution_result";

export interface ThreadMessage {
  message_id: string;
  session_id: string;
  seq: number;
  type: MessageType;
  content: string;
  created_at: string;
  turn_id: string | null;
  trace_id: string | null;
  actor_id: string | null;
  execution_id: string | null;
  approval_id: string | null;
  card: Record<string, unknown> | null;
  tool_name: string | null;
  status: string | null;
  result: Record<string, unknown> | null;
  grounding: Grounding | null;
  reason: string | null;
}

export type Authority = "authoritative" | "derived" | "unverified" | "not_applicable";

export interface GroundingSource {
  span_id: string;
  tool_name: string;
  args_summary: string | null;
  result_summary: string | null;
}

export interface Grounding {
  authority: Authority;
  sources: GroundingSource[];
  diagnostic: string | null;
}

export type AlertStatus = "open" | "acknowledged" | "closed";
export type AlertSeverity = "info" | "warning" | "critical";

export interface AlertSource {
  source_id: string;
  tool_name: string;
  args_summary: string | null;
  result_summary: string | null;
  evidence: string | null;
}

export interface AlertGrounding {
  authority: Authority;
  sources: AlertSource[];
  diagnostic: string | null;
}

export interface Alert {
  alert_id: string;
  check_name: string;
  dedupe_key: string;
  title: string;
  severity: AlertSeverity;
  status: AlertStatus;
  metric: string;
  value: number | string | null;
  threshold: number | string | null;
  entities: Record<string, unknown>;
  cause: string | null;
  grounding: AlertGrounding;
  created_at: string;
  updated_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
}

export interface SessionSummary {
  session_id: string;
  title: string | null;
  created_at: string;
  last_message_preview: string | null;
  message_count: number;
}

export interface SessionDetail {
  session_id: string;
  title: string | null;
  created_at: string;
  message_count: number;
}

export type StreamEvent =
  | { kind: "thread.append"; message: ThreadMessage }
  | { kind: "token"; text: string }
  | { kind: "tool"; name: string; phase: string }
  | { kind: "done"; turnId: string }
  | { kind: "error"; message: string };

export interface HealthComponent {
  status: string;
  error?: string;
  model?: string;
  checked?: string;
}

export interface HealthStatus {
  status: string;
  app: string;
  environment: string;
  configured_mcp_servers: string[];
  agent_ready: boolean;
  components: {
    mongo?: HealthComponent;
    sandbox?: HealthComponent;
    model?: HealthComponent;
    [key: string]: HealthComponent | undefined;
  };
}

export interface McpServerHealth {
  status: string;
  error?: string;
  tool_count?: number;
  tools?: string[];
  agent_allowed_tool_count?: number;
  agent_allowed_tools?: string[];
  [key: string]: unknown;
}

export interface McpHealth {
  status: string;
  servers: Record<string, McpServerHealth>;
}

export interface TraceSpan {
  kind: "model_call" | "tool_call" | "route_decision" | "policy_denial";
  name: string | null;
  status: string;
  ts: number;
  duration_ms: number | null;
  args_summary: string | null;
  result_summary: string | null;
  evidence: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  span_id: string;
  artifact_id: string | null;
  approval_id: string | null;
  error_message: string | null;
}

export interface TraceTimeline {
  trace_id: string;
  session_id: string;
  turn_id: string;
  started_at: number;
  ended_at: number | null;
  duration_ms: number | null;
  tokens_in_total: number | null;
  tokens_out_total: number | null;
  span_count: number;
  spans: TraceSpan[];
}

export interface ArtifactSummary {
  id: string;
  kind: string;
  mime_type: string;
  src: string;
  tool_name: string | null;
  turn_id: string | null;
  trace_id: string | null;
  created_at: string;
  message_id: string;
}
