import type { Agent, Environment, Session } from "../src/index.js";

export interface RecordedRequest {
  method: string;
  path: string;
  query: URLSearchParams;
  headers: Record<string, string>;
  body: unknown;
}

type Responder = (req: Request, parsed: unknown) => Response | Promise<Response>;

export class MockServer {
  readonly requests: RecordedRequest[] = [];
  private readonly routes = new Map<string, Responder>();

  register(method: string, path: string, responder: Responder): void {
    this.routes.set(`${method.toUpperCase()} ${path}`, responder);
  }

  json(method: string, path: string, status: number, body: unknown): void {
    this.register(method, path, () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      }),
    );
  }

  fetch: typeof fetch = async (input, init) => {
    const url = typeof input === "string" || input instanceof URL ? new URL(input.toString()) : new URL(input.url);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    const headers: Record<string, string> = {};
    const rawHeaders = init?.headers ?? (input instanceof Request ? input.headers : undefined);
    if (rawHeaders) {
      new Headers(rawHeaders).forEach((value, key) => {
        headers[key] = value;
      });
    }

    let bodyText: string | null = null;
    if (init?.body != null) {
      bodyText = typeof init.body === "string" ? init.body : String(init.body);
    } else if (input instanceof Request) {
      bodyText = await input.clone().text();
    }

    let parsedBody: unknown = null;
    if (bodyText) {
      try {
        parsedBody = JSON.parse(bodyText);
      } catch {
        parsedBody = bodyText;
      }
    }

    this.requests.push({
      method,
      path: url.pathname,
      query: url.searchParams,
      headers,
      body: parsedBody,
    });

    const responder = this.routes.get(`${method} ${url.pathname}`);
    if (!responder) {
      return new Response(
        JSON.stringify({ detail: `No mock for ${method} ${url.pathname}` }),
        { status: 501, headers: { "content-type": "application/json" } },
      );
    }
    const req =
      input instanceof Request
        ? input
        : new Request(url.toString(), init as RequestInit | undefined);
    return responder(req, parsedBody);
  };
}

const now = (): string => new Date().toISOString();
const uuid = (): string =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });

export function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: uuid(),
    type: "agent",
    name: "demo",
    description: null,
    system: null,
    model: "claude-sonnet-4-5",
    runtime: "claude-code",
    environment_id: null,
    skills: [],
    mcp_servers: [],
    metadata: {},
    version: 1,
    archived_at: null,
    created_at: now(),
    updated_at: now(),
    ...overrides,
  };
}

export function makeEnvironment(
  overrides: Partial<Environment> = {},
): Environment {
  return {
    id: uuid(),
    type: "environment",
    name: "demo-env",
    packages: {},
    env_vars: {},
    setup_script: null,
    networking: { type: "unrestricted" },
    version: 1,
    archived_at: null,
    created_at: now(),
    updated_at: now(),
    ...overrides,
  };
}

export function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: uuid(),
    agent_id: uuid(),
    environment_id: null,
    runtime: "claude-code",
    status: "completed",
    exit_code: 0,
    created_at: now(),
    updated_at: now(),
    resources: [],
    turn_count: 1,
    current_turn: 1,
    ...overrides,
  };
}

export { uuid };
