import { raiseForStatus } from "./errors.js";
import { VERSION } from "./version.js";

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
// Browsers strip `User-Agent` per the Fetch spec (forbidden header name); this
// header is only observed by Node/Bun/Deno callers. A future browser-targeted
// build should switch to a non-forbidden name like `X-Aod-Client`.
const USER_AGENT = `aod-sdk-ts/${VERSION}`;

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
      "User-Agent": USER_AGENT,
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
    const { signal, cleanup } = linkSignal(controller, opts.signal);
    init.signal = signal;

    // Keep the timer covering parseBody too — fetch resolves once headers
    // arrive, so a slow body would otherwise read forever.
    try {
      const response = await this.fetchFn(url, init);
      const body = await parseBody(response);
      raiseForStatus(response.status, body, method, url);
      return body as T;
    } finally {
      clearTimeout(timeoutId);
      cleanup();
    }
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

// Without explicit removal, the listener (closing over `controller`) leaks
// for as long as `external` lives — a real issue when callers reuse one
// AbortController across many requests. The returned `cleanup` removes the
// listener on every exit path.
export function linkSignal(
  controller: AbortController,
  external?: AbortSignal,
): { signal: AbortSignal; cleanup: () => void } {
  const noop = () => {};
  if (!external) return { signal: controller.signal, cleanup: noop };
  if (external.aborted) {
    controller.abort(external.reason);
    return { signal: controller.signal, cleanup: noop };
  }
  const onAbort = () => controller.abort(external.reason);
  external.addEventListener("abort", onAbort);
  return {
    signal: controller.signal,
    cleanup: () => external.removeEventListener("abort", onAbort),
  };
}
