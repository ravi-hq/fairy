import type { HttpClient } from "../http.js";
import type { Agent, AgentVersion, McpServer } from "../types.js";

export interface AgentCreateParams {
  name: string;
  model: string;
  runtime: string;
  system?: string;
  description?: string;
  environment_id?: string;
  skills?: Record<string, unknown>[];
  mcp_servers?: McpServer[];
  metadata?: Record<string, unknown>;
}

export interface AgentUpdateParams {
  version: number;
  name?: string;
  model?: string;
  runtime?: string;
  system?: string;
  description?: string;
  // Pass `null` to detach the current environment from the agent.
  environment_id?: string | null;
  skills?: Record<string, unknown>[];
  mcp_servers?: McpServer[];
  metadata?: Record<string, unknown>;
}

interface ListResponse<T> {
  data: T[];
}

export class Agents {
  constructor(private readonly http: HttpClient) {}

  async list(opts: { signal?: AbortSignal } = {}): Promise<Agent[]> {
    const body = await this.http.request<ListResponse<Agent>>("GET", "/agents", {
      signal: opts.signal,
    });
    return body.data;
  }

  async create(
    params: AgentCreateParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Agent> {
    return this.http.request<Agent>("POST", "/agents", {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async get(agentId: string, opts: { signal?: AbortSignal } = {}): Promise<Agent> {
    return this.http.request<Agent>("GET", `/agents/${agentId}`, {
      signal: opts.signal,
    });
  }

  async update(
    agentId: string,
    params: AgentUpdateParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Agent> {
    return this.http.request<Agent>("PUT", `/agents/${agentId}`, {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async archive(agentId: string, opts: { signal?: AbortSignal } = {}): Promise<Agent> {
    return this.http.request<Agent>("POST", `/agents/${agentId}/archive`, {
      signal: opts.signal,
    });
  }

  async versions(
    agentId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<AgentVersion[]> {
    const body = await this.http.request<ListResponse<AgentVersion>>(
      "GET",
      `/agents/${agentId}/versions`,
      { signal: opts.signal },
    );
    return body.data;
  }
}

function stripUndefined<T extends object>(obj: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) out[k] = v;
  }
  return out as Partial<T>;
}
