export type SessionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "terminated";

export type TurnStatus = "pending" | "running" | "completed" | "failed";

export type NetworkingType = "unrestricted" | "limited";

export type PackageManager = "apt" | "cargo" | "gem" | "go" | "npm" | "pip";

export interface McpServer {
  name: string;
  type: "url" | "stdio";
  url?: string | null;
  command?: string | null;
}

export interface Networking {
  type: NetworkingType;
  allowed_hosts?: string[];
}

export interface SessionResource {
  type: "github_repository";
  url: string;
  mount_path?: string | null;
}

export interface SessionResourceInput extends SessionResource {
  token?: string | null;
}

export interface Agent {
  id: string;
  type: "agent";
  name: string;
  description?: string | null;
  system?: string | null;
  model: string;
  runtime: string;
  environment_id?: string | null;
  skills: Record<string, unknown>[];
  mcp_servers: McpServer[];
  metadata: Record<string, unknown>;
  version: number;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentVersion {
  id: string;
  type: "agent";
  name: string;
  description?: string | null;
  system?: string | null;
  model: string;
  runtime: string;
  environment_id?: string | null;
  skills: Record<string, unknown>[];
  mcp_servers: McpServer[];
  metadata: Record<string, unknown>;
  version: number;
  created_at: string;
}

export interface Environment {
  id: string;
  type: "environment";
  name: string;
  packages: Record<string, string[]>;
  env_vars: Record<string, string>;
  setup_script?: string | null;
  networking: Networking;
  version: number;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface EnvironmentVersion {
  id: string;
  type: "environment";
  name: string;
  packages: Record<string, string[]>;
  env_vars: Record<string, string>;
  setup_script?: string | null;
  networking: Networking;
  version: number;
  created_at: string;
}

export interface Session {
  id: string;
  agent_id?: string | null;
  environment_id?: string | null;
  runtime: string;
  status: SessionStatus;
  exit_code?: number | null;
  created_at: string;
  updated_at: string;
  resources: SessionResource[];
  turn_count: number;
  current_turn?: number | null;
}

export interface SessionTurn {
  turn_number: number;
  prompt: string;
  status: TurnStatus;
  exit_code?: number | null;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
}

export interface SessionAck {
  id: string;
  status: SessionStatus;
  stream_url?: string | null;
  environment_id?: string | null;
  resources?: SessionResource[];
  current_turn?: number | null;
}

export type StreamEventType =
  | "start"
  | "turn_start"
  | "output"
  | "stage"
  | "exit"
  | "error"
  | "terminated"
  | "stale";

export interface StreamEvent {
  type: StreamEventType;
  id?: number | null;
  extra: Record<string, unknown>;
}

export function streamEventFromPayload(payload: Record<string, unknown>): StreamEvent {
  const { type, id, ...rest } = payload;
  return {
    type: (typeof type === "string" ? type : "output") as StreamEventType,
    id: typeof id === "number" ? id : null,
    extra: rest,
  };
}
