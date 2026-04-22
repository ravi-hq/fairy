from __future__ import annotations

import os
from typing import Any

import httpx

from ._http import build_async_client, build_sync_client, check_response
from .resources import (
    Agents,
    AsyncAgents,
    AsyncEnvironments,
    AsyncSessions,
    Environments,
    Sessions,
)

_DEFAULT_BASE_URL = "http://localhost:8777"


def _resolve(base_url: str | None, token: str | None) -> tuple[str, str]:
    base_url = base_url or os.environ.get("AOD_API_URL") or _DEFAULT_BASE_URL
    token = token or os.environ.get("AOD_API_TOKEN")
    if not token:
        raise ValueError("Missing API token. Pass token=... or set the AOD_API_TOKEN env var.")
    return base_url, token


class Client:
    """Synchronous client for the Agent on Demand API.

    Reads AOD_API_URL and AOD_API_TOKEN from the environment when the matching
    constructor argument is not provided.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved_base, resolved_token = _resolve(base_url, token)
        self._http = build_sync_client(
            resolved_base, resolved_token, timeout=timeout, transport=transport
        )
        self.agents = Agents(self._http)
        self.environments = Environments(self._http)
        self.sessions = Sessions(self._http)

    def health(self) -> dict[str, Any]:
        return check_response(self._http.get("/health"))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class AsyncClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: httpx.Timeout | float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_base, resolved_token = _resolve(base_url, token)
        self._http = build_async_client(
            resolved_base, resolved_token, timeout=timeout, transport=transport
        )
        self.agents = AsyncAgents(self._http)
        self.environments = AsyncEnvironments(self._http)
        self.sessions = AsyncSessions(self._http)

    async def health(self) -> dict[str, Any]:
        return check_response(await self._http.get("/health"))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()
