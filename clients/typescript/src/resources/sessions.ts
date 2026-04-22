import type { HttpClient } from "../http.js";
import { createStreamHandle, type StreamHandle } from "../stream.js";
import type {
  Session,
  SessionAck,
  SessionResourceInput,
  SessionTurn,
} from "../types.js";

export interface SessionCreateParams {
  agent_id: string;
  prompt: string;
  environment_id?: string;
  timeout?: number;
  resources?: SessionResourceInput[];
}

export interface SessionPromptParams {
  prompt: string;
  timeout?: number;
}

export interface SessionStreamOptions {
  since?: number;
  signal?: AbortSignal;
}

interface ListResponse<T> {
  data: T[];
}

export class Sessions {
  constructor(private readonly http: HttpClient) {}

  async list(opts: { signal?: AbortSignal } = {}): Promise<Session[]> {
    const body = await this.http.request<ListResponse<Session>>("GET", "/sessions", {
      signal: opts.signal,
    });
    return body.data;
  }

  async create(
    params: SessionCreateParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<SessionAck> {
    return this.http.request<SessionAck>("POST", "/sessions", {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async get(sessionId: string, opts: { signal?: AbortSignal } = {}): Promise<Session> {
    return this.http.request<Session>("GET", `/sessions/${sessionId}`, {
      signal: opts.signal,
    });
  }

  async prompt(
    sessionId: string,
    params: SessionPromptParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<SessionAck> {
    return this.http.request<SessionAck>("POST", `/sessions/${sessionId}/prompt`, {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async turns(
    sessionId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<SessionTurn[]> {
    const body = await this.http.request<ListResponse<SessionTurn>>(
      "GET",
      `/sessions/${sessionId}/turns`,
      { signal: opts.signal },
    );
    return body.data;
  }

  async terminate(
    sessionId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<SessionAck> {
    return this.http.request<SessionAck>("POST", `/sessions/${sessionId}/terminate`, {
      signal: opts.signal,
    });
  }

  async delete(
    sessionId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<void> {
    await this.http.request<null>("DELETE", `/sessions/${sessionId}/delete`, {
      signal: opts.signal,
    });
  }

  async stream(
    sessionId: string,
    opts: SessionStreamOptions = {},
  ): Promise<StreamHandle> {
    const controller = new AbortController();
    const signal = linkSignal(controller, opts.signal);
    const query = opts.since !== undefined ? { since: opts.since } : undefined;
    const { response, url } = await this.http.rawStream(
      `/sessions/${sessionId}/stream`,
      { query, signal },
    );
    return createStreamHandle(response, url, controller);
  }
}

function stripUndefined<T extends object>(obj: T): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) out[k] = v;
  }
  return out as Partial<T>;
}

function linkSignal(
  controller: AbortController,
  external?: AbortSignal,
): AbortSignal {
  if (!external) return controller.signal;
  if (external.aborted) {
    controller.abort(external.reason);
    return controller.signal;
  }
  external.addEventListener(
    "abort",
    () => controller.abort(external.reason),
    { once: true },
  );
  return controller.signal;
}
