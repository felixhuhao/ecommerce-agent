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
  reason: string | null;
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
