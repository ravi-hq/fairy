import { HttpClient, type FetchFn } from "./http.js";
import { Agents, Environments, Sessions } from "./resources/index.js";

const DEFAULT_BASE_URL = "http://localhost:8777";

export interface ClientOptions {
  baseUrl?: string;
  token?: string;
  fetch?: FetchFn;
  timeoutMs?: number;
}

export class Client {
  readonly agents: Agents;
  readonly environments: Environments;
  readonly sessions: Sessions;
  private readonly http: HttpClient;

  constructor(opts: ClientOptions = {}) {
    const { baseUrl, token } = resolveConfig(opts);
    this.http = new HttpClient({
      baseUrl,
      token,
      fetch: opts.fetch,
      timeoutMs: opts.timeoutMs,
    });
    this.agents = new Agents(this.http);
    this.environments = new Environments(this.http);
    this.sessions = new Sessions(this.http);
  }

  async health(opts: { signal?: AbortSignal } = {}): Promise<Record<string, unknown>> {
    return this.http.request<Record<string, unknown>>("GET", "/health", {
      signal: opts.signal,
    });
  }
}

function resolveConfig(opts: ClientOptions): { baseUrl: string; token: string } {
  const baseUrl = opts.baseUrl ?? readEnv("AOD_API_URL") ?? DEFAULT_BASE_URL;
  const token = opts.token ?? readEnv("AOD_API_TOKEN");
  if (!token) {
    throw new Error(
      "Missing API token. Pass token=... or set the AOD_API_TOKEN env var.",
    );
  }
  return { baseUrl, token };
}

function readEnv(key: string): string | undefined {
  if (typeof process !== "undefined" && process.env) {
    return process.env[key];
  }
  return undefined;
}
