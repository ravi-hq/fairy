export class AodError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AodError";
  }
}

export class AodHTTPError extends AodError {
  readonly statusCode: number;
  readonly detail: unknown;
  readonly method: string;
  readonly url: string;

  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(`${method} ${url} -> ${statusCode}: ${formatDetail(detail)}`);
    this.name = "AodHTTPError";
    this.statusCode = statusCode;
    this.detail = detail;
    this.method = method;
    this.url = url;
  }
}

export class AuthError extends AodHTTPError {
  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(statusCode, detail, method, url);
    this.name = "AuthError";
  }
}

export class NotFoundError extends AodHTTPError {
  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(statusCode, detail, method, url);
    this.name = "NotFoundError";
  }
}

export class ConflictError extends AodHTTPError {
  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(statusCode, detail, method, url);
    this.name = "ConflictError";
  }
}

export class ValidationError extends AodHTTPError {
  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(statusCode, detail, method, url);
    this.name = "ValidationError";
  }
}

export class RateLimitError extends AodHTTPError {
  readonly limit: number | null;
  readonly active: number | null;

  constructor(
    statusCode: number,
    detail: unknown,
    method: string,
    url: string,
    { limit, active }: { limit?: number | null; active?: number | null } = {},
  ) {
    super(statusCode, detail, method, url);
    this.name = "RateLimitError";
    this.limit = limit ?? null;
    this.active = active ?? null;
  }
}

export class ServerError extends AodHTTPError {
  constructor(statusCode: number, detail: unknown, method: string, url: string) {
    super(statusCode, detail, method, url);
    this.name = "ServerError";
  }
}

function formatDetail(detail: unknown): string {
  if (detail == null) return "null";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

export function raiseForStatus(
  status: number,
  body: unknown,
  method: string,
  url: string,
): void {
  if (status >= 200 && status < 300) return;

  const detail =
    body && typeof body === "object" && "detail" in body
      ? (body as Record<string, unknown>).detail
      : body;

  if (status === 401 || status === 403) {
    throw new AuthError(status, detail, method, url);
  }
  if (status === 404) {
    throw new NotFoundError(status, detail, method, url);
  }
  if (status === 409) {
    throw new ConflictError(status, detail, method, url);
  }
  if (status === 422) {
    throw new ValidationError(status, detail, method, url);
  }
  if (status === 429) {
    const limit =
      body && typeof body === "object" && typeof (body as Record<string, unknown>).limit === "number"
        ? ((body as Record<string, unknown>).limit as number)
        : null;
    const active =
      body && typeof body === "object" && typeof (body as Record<string, unknown>).active === "number"
        ? ((body as Record<string, unknown>).active as number)
        : null;
    throw new RateLimitError(status, detail, method, url, { limit, active });
  }
  if (status >= 500) {
    throw new ServerError(status, detail, method, url);
  }
  throw new AodHTTPError(status, detail, method, url);
}
