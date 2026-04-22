from __future__ import annotations

import json
from typing import Any

import httpx

from .errors import raise_for_status

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
USER_AGENT = "aod-sdk/0.1.0"


def build_headers(token: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }
    if extra:
        headers.update(extra)
    return headers


def parse_body(response: httpx.Response) -> Any:
    """Parse a response body. Empty body returns None; non-JSON returns text."""
    if not response.content:
        return None
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return response.json()
        except json.JSONDecodeError:
            return response.text
    return response.text


def check_response(response: httpx.Response) -> Any:
    body = parse_body(response)
    raise_for_status(response.status_code, body, response.request.method, str(response.request.url))
    return body


def build_sync_client(
    base_url: str,
    token: str,
    *,
    timeout: httpx.Timeout | float | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=build_headers(token),
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        transport=transport,
    )


def build_async_client(
    base_url: str,
    token: str,
    *,
    timeout: httpx.Timeout | float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=build_headers(token),
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        transport=transport,
    )
