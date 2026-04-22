import type { HttpClient } from "../http.js";
import type { Environment, EnvironmentVersion, Networking } from "../types.js";

export interface EnvironmentCreateParams {
  name: string;
  packages?: Record<string, string[]>;
  env_vars?: Record<string, string>;
  setup_script?: string;
  networking?: Networking;
}

export interface EnvironmentUpdateParams {
  version: number;
  name?: string;
  packages?: Record<string, string[]>;
  env_vars?: Record<string, string>;
  setup_script?: string;
  networking?: Networking;
}

interface ListResponse<T> {
  data: T[];
}

export class Environments {
  constructor(private readonly http: HttpClient) {}

  async list(opts: { signal?: AbortSignal } = {}): Promise<Environment[]> {
    const body = await this.http.request<ListResponse<Environment>>(
      "GET",
      "/environments",
      { signal: opts.signal },
    );
    return body.data;
  }

  async create(
    params: EnvironmentCreateParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Environment> {
    return this.http.request<Environment>("POST", "/environments", {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async get(
    environmentId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Environment> {
    return this.http.request<Environment>("GET", `/environments/${environmentId}`, {
      signal: opts.signal,
    });
  }

  async update(
    environmentId: string,
    params: EnvironmentUpdateParams,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Environment> {
    return this.http.request<Environment>("PUT", `/environments/${environmentId}`, {
      body: stripUndefined(params),
      signal: opts.signal,
    });
  }

  async archive(
    environmentId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<Environment> {
    return this.http.request<Environment>(
      "POST",
      `/environments/${environmentId}/archive`,
      { signal: opts.signal },
    );
  }

  async delete(
    environmentId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<void> {
    await this.http.request<null>("DELETE", `/environments/${environmentId}/delete`, {
      signal: opts.signal,
    });
  }

  async versions(
    environmentId: string,
    opts: { signal?: AbortSignal } = {},
  ): Promise<EnvironmentVersion[]> {
    const body = await this.http.request<ListResponse<EnvironmentVersion>>(
      "GET",
      `/environments/${environmentId}/versions`,
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
