from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from uuid import UUID

import httpx

from .._http import check_response
from ..models import (
    GithubRepoResource,
    GithubRepoResourceInput,
    Session,
    SessionAck,
    SessionTurn,
    StreamEvent,
)
from ..stream import aiter_sse, iter_sse


def _normalize_resources(
    resources: list[GithubRepoResourceInput] | None,
) -> list[dict[str, Any]] | None:
    if resources is None:
        return None
    out: list[dict[str, Any]] = []
    for entry in resources:
        if isinstance(entry, GithubRepoResource):
            out.append(entry.model_dump(exclude_none=True))
        else:
            # dicts bypass client-side validation intentionally
            out.append(entry)
    return out


def _create_body(
    *,
    agent_id: str | UUID,
    prompt: str,
    environment_id: str | UUID | None = None,
    timeout: int | None = None,
    resources: list[GithubRepoResourceInput] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"agent_id": str(agent_id), "prompt": prompt}
    if environment_id is not None:
        body["environment_id"] = str(environment_id)
    if timeout is not None:
        body["timeout"] = timeout
    normalized = _normalize_resources(resources)
    if normalized is not None:
        body["resources"] = normalized
    return body


def _prompt_body(*, prompt: str, timeout: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"prompt": prompt}
    if timeout is not None:
        body["timeout"] = timeout
    return body


def _stream_params(since: int | None) -> dict[str, Any]:
    return {"since": since} if since is not None else {}


class Sessions:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list(self) -> list[Session]:
        body = check_response(self._client.get("/sessions"))
        return [Session.model_validate(s) for s in body["data"]]

    def create(
        self,
        *,
        agent_id: str | UUID,
        prompt: str,
        environment_id: str | UUID | None = None,
        timeout: int | None = None,
        resources: list[GithubRepoResourceInput] | None = None,
    ) -> SessionAck:
        body = _create_body(
            agent_id=agent_id,
            prompt=prompt,
            environment_id=environment_id,
            timeout=timeout,
            resources=resources,
        )
        return SessionAck.model_validate(check_response(self._client.post("/sessions", json=body)))

    def get(self, session_id: str | UUID) -> Session:
        return Session.model_validate(check_response(self._client.get(f"/sessions/{session_id}")))

    def prompt(
        self, session_id: str | UUID, *, prompt: str, timeout: int | None = None
    ) -> SessionAck:
        body = _prompt_body(prompt=prompt, timeout=timeout)
        return SessionAck.model_validate(
            check_response(self._client.post(f"/sessions/{session_id}/prompt", json=body))
        )

    def turns(self, session_id: str | UUID) -> list[SessionTurn]:
        body = check_response(self._client.get(f"/sessions/{session_id}/turns"))
        return [SessionTurn.model_validate(t) for t in body["data"]]

    def terminate(self, session_id: str | UUID) -> SessionAck:
        return SessionAck.model_validate(
            check_response(self._client.post(f"/sessions/{session_id}/terminate"))
        )

    def delete(self, session_id: str | UUID) -> None:
        check_response(self._client.delete(f"/sessions/{session_id}/delete"))

    @contextmanager
    def stream(
        self, session_id: str | UUID, *, since: int | None = None
    ) -> Iterator[Iterator[StreamEvent]]:
        """Open an SSE stream for a session.

        Usage:
            with client.sessions.stream(session_id) as events:
                for event in events:
                    ...
        """
        with self._client.stream(
            "GET",
            f"/sessions/{session_id}/stream",
            params=_stream_params(since),
            headers={"Accept": "text/event-stream"},
        ) as response:
            yield iter_sse(response)


class AsyncSessions:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list(self) -> list[Session]:
        body = check_response(await self._client.get("/sessions"))
        return [Session.model_validate(s) for s in body["data"]]

    async def create(
        self,
        *,
        agent_id: str | UUID,
        prompt: str,
        environment_id: str | UUID | None = None,
        timeout: int | None = None,
        resources: list[GithubRepoResourceInput] | None = None,
    ) -> SessionAck:
        body = _create_body(
            agent_id=agent_id,
            prompt=prompt,
            environment_id=environment_id,
            timeout=timeout,
            resources=resources,
        )
        return SessionAck.model_validate(
            check_response(await self._client.post("/sessions", json=body))
        )

    async def get(self, session_id: str | UUID) -> Session:
        return Session.model_validate(
            check_response(await self._client.get(f"/sessions/{session_id}"))
        )

    async def prompt(
        self, session_id: str | UUID, *, prompt: str, timeout: int | None = None
    ) -> SessionAck:
        body = _prompt_body(prompt=prompt, timeout=timeout)
        return SessionAck.model_validate(
            check_response(await self._client.post(f"/sessions/{session_id}/prompt", json=body))
        )

    async def turns(self, session_id: str | UUID) -> list[SessionTurn]:
        body = check_response(await self._client.get(f"/sessions/{session_id}/turns"))
        return [SessionTurn.model_validate(t) for t in body["data"]]

    async def terminate(self, session_id: str | UUID) -> SessionAck:
        return SessionAck.model_validate(
            check_response(await self._client.post(f"/sessions/{session_id}/terminate"))
        )

    async def delete(self, session_id: str | UUID) -> None:
        check_response(await self._client.delete(f"/sessions/{session_id}/delete"))

    @asynccontextmanager
    async def stream(
        self, session_id: str | UUID, *, since: int | None = None
    ) -> AsyncIterator[AsyncIterator[StreamEvent]]:
        """Open an SSE stream for a session.

        Usage:
            async with client.sessions.stream(session_id) as events:
                async for event in events:
                    ...
        """
        async with self._client.stream(
            "GET",
            f"/sessions/{session_id}/stream",
            params=_stream_params(since),
            headers={"Accept": "text/event-stream"},
        ) as response:
            yield aiter_sse(response)
