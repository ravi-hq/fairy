from __future__ import annotations

from typing import Any


class AodError(Exception):
    """Base class for all errors raised by the SDK."""


class AodHTTPError(AodError):
    def __init__(self, status_code: int, detail: Any, method: str, url: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.method = method
        self.url = url
        super().__init__(f"{method} {url} -> {status_code}: {detail!r}")


class AuthError(AodHTTPError):
    """401/403."""


class NotFoundError(AodHTTPError):
    """404."""


class ConflictError(AodHTTPError):
    """409 — archived row, terminal session, or stale optimistic-concurrency version."""


class ValidationError(AodHTTPError):
    """422 — pydantic validation failure on the server."""


class RateLimitError(AodHTTPError):
    """429 — per-user concurrent session limit reached."""

    def __init__(
        self,
        status_code: int,
        detail: Any,
        method: str,
        url: str,
        *,
        limit: int | None = None,
        active: int | None = None,
    ) -> None:
        super().__init__(status_code, detail, method, url)
        self.limit = limit
        self.active = active


class ServerError(AodHTTPError):
    """5xx."""


def raise_for_status(status_code: int, body: Any, method: str, url: str) -> None:
    if 200 <= status_code < 300:
        return
    detail = body.get("detail", body) if isinstance(body, dict) else body
    if status_code in (401, 403):
        raise AuthError(status_code, detail, method, url)
    if status_code == 404:
        raise NotFoundError(status_code, detail, method, url)
    if status_code == 409:
        raise ConflictError(status_code, detail, method, url)
    if status_code == 422:
        raise ValidationError(status_code, detail, method, url)
    if status_code == 429:
        limit = body.get("limit") if isinstance(body, dict) else None
        active = body.get("active") if isinstance(body, dict) else None
        raise RateLimitError(status_code, detail, method, url, limit=limit, active=active)
    if status_code >= 500:
        raise ServerError(status_code, detail, method, url)
    raise AodHTTPError(status_code, detail, method, url)
