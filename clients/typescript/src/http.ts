import { raiseForStatus } from "./errors.js";

export type FetchFn = typeof fetch;

export interface HttpClientOptions {
  baseUrl: string;
  token: string;
  fetch?: FetchFn;
  timeoutMs?: number;
}

export interface RequestOptions {
  body?: unknown;
  query?: Record<string, unknown> | undefined;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export class HttpClient {
  readonly baseUrl: string;
  readonly token: string;
  private readonly fetchFn: FetchFn;
  private readonly timeoutMs: number;

  constructor(opts: HttpClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "");
    this.token = opts.token;
    this.fetchFn = opts.fetch ?? globalThis.fetch.bind(globalThis);
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  buildUrl(path: string, query?: Record<string, unknown>): string {
    const suffix = path.startsWith("/") ? path : `/${path}`;
    const qs = buildQueryString(query);
    return `${this.baseUrl}${suffix}${qs}`;
  }

  buildHeaders(extra?: Record<string, string>): Headers {
    const headers = new Headers({
      Authorization: `Bearer ${this.token}`,
    });
    if (extra) {
      for (const [k, v] of Object.entries(extra)) {
        headers.set(k, v);
      }
    }
    return headers;
  }

  async request<T>(
    method: string,
    path: string,
    opts: RequestOptions = {},
  ): Promise<T> {
    const url = this.buildUrl(path, opts.query);
    const headers = this.buildHeaders(opts.headers);
    const init: RequestInit = { method, headers };

    if (opts.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(opts.body);
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);
    const signal = linkSignal(controller, opts.signal);
    init.signal = signal;

    let response: Response;
    try {
      response = await this.fetchFn(url, init);
    } finally {
      clearTimeout(timeoutId);
    }

    const body = await parseBody(response);
    raiseForStatus(response.status, body, method, url);
    return body as T;
  }

  async rawStream(
    path: string,
    opts: { query?: Record<string, unknown>; signal?: AbortSignal } = {},
  ): Promise<{ response: Response; url: string }> {
    const url = this.buildUrl(path, opts.query);
    const headers = this.buildHeaders({ Accept: "text/event-stream" });
    const response = await this.fetchFn(url, {
      method: "GET",
      headers,
      signal: opts.signal,
    });
    return { response, url };
  }
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}

function buildQueryString(query?: Record<string, unknown>): string {
  if (!query) return "";
  const params = new URLSearchParams();
  let count = 0;
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    if (Array.isArray(value)) {
      for (const v of value) {
        params.append(key, String(v));
        count++;
      }
    } else {
      params.append(key, String(value));
      count++;
    }
  }
  return count === 0 ? "" : `?${params.toString()}`;
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
