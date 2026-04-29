from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel

from .._http import check_response
from ..models import (
    TERMINAL_EVENT_TYPES,
    GithubRepoResource,
    GithubRepoResourceInput,
    RunResult,
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


def _stream_headers(since: int | None) -> dict[str, str]:
    """Build the request headers for `GET /sessions/{id}/stream`.

    `Last-Event-ID` is the standard SSE resume header. The server reads
    either it or the `?since=` query parameter; sending the header too
    makes resume work transparently when an intermediary strips query
    params (some SSE-aware proxies do).
    """
    headers = {"Accept": "text/event-stream"}
    if since is not None:
        headers["Last-Event-ID"] = str(since)
    return headers


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
            headers=_stream_headers(since),
        ) as response:
            yield iter_sse(response)

    def stream_resumable(
        self,
        session_id: str | UUID,
        *,
        since: int | None = None,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_backoff: float = 30.0,
    ) -> Iterator[StreamEvent]:
        """Like `stream()`, but auto-reconnects on transient HTTP drops.

        Catches `httpx.RemoteProtocolError` / `httpx.ReadError` /
        `httpx.ConnectError` / `httpx.TimeoutException` (covers proxy
        idle-timeout `ReadTimeout`s), sleeps with exponential backoff,
        and reconnects using `since=<last seen event id>`. Each event
        still ships exactly once because the server cursor advances on
        the response side. Stops retrying after `max_retries`
        *consecutive* failures and re-raises the last exception — any
        successfully yielded event resets the failure counter.

        Yields `StreamEvent`s directly (not a context manager) — caller
        drives the loop.
        """
        cursor = since
        attempt = 0
        backoff = initial_backoff
        while True:
            try:
                with self.stream(session_id, since=cursor) as events:
                    for event in events:
                        if event.id is not None:
                            cursor = event.id
                        yield event
                        # Progress made — reset the consecutive-failure budget.
                        attempt = 0
                        backoff = initial_backoff
                return
            except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.TimeoutException,
            ):
                attempt += 1
                if attempt > max_retries:
                    raise
                time.sleep(min(backoff, max_backoff))
                backoff *= backoff_multiplier

    def run(
        self,
        *,
        agent_id: str | UUID,
        prompt: str,
        environment_id: str | UUID | None = None,
        timeout: int | None = None,
        resources: list[dict[str, Any]] | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
        collect_events: bool = True,
    ) -> RunResult:
        """Create a session, stream until the first turn finishes, and
        return the final session record (plus optionally the events).

        Equivalent to `create() + stream()` + `get()` chained, with the
        stream loop closed at the first terminal event (`exit`, `error`,
        or `terminated`). The same constructor knobs as `create()` are
        accepted; `on_event` is called for each event as it arrives,
        useful for live progress.

        Set `collect_events=False` to avoid retaining events in memory
        for long sessions where the caller has already handled them via
        `on_event`.
        """
        ack = self.create(
            agent_id=agent_id,
            prompt=prompt,
            environment_id=environment_id,
            timeout=timeout,
            resources=resources,
        )
        return self.wait_for_completion(
            ack.id, on_event=on_event, collect_events=collect_events
        )

    def wait_for_completion(
        self,
        session_id: str | UUID,
        *,
        since: int | None = None,
        on_event: Callable[[StreamEvent], None] | None = None,
        collect_events: bool = True,
    ) -> RunResult:
        """Stream an existing session until the first terminal event
        (`exit`, `error`, `terminated`), then return its final record.

        Useful when sessions are kicked off elsewhere (different process,
        another caller, prior `create()` you didn't await) and you want
        to block on completion. Pass `since=<event id>` to resume from
        a known cursor instead of replaying the full log.
        """
        events: list[StreamEvent] = []
        with self.stream(session_id, since=since) as stream:
            for event in stream:
                if on_event is not None:
                    on_event(event)
                if collect_events:
                    events.append(event)
                if event.type in TERMINAL_EVENT_TYPES:
                    break
        session = self.get(session_id)
        return RunResult(session=session, events=events)


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
            headers=_stream_headers(since),
        ) as response:
            yield aiter_sse(response)

    async def stream_resumable(
        self,
        session_id: str | UUID,
        *,
        since: int | None = None,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        backoff_multiplier: float = 2.0,
        max_backoff: float = 30.0,
    ) -> AsyncIterator[StreamEvent]:
        """See `Sessions.stream_resumable` — async variant. Yields events
        directly (not a context manager); use `async for`.
        """
        cursor = since
        attempt = 0
        backoff = initial_backoff
        while True:
            try:
                async with self.stream(session_id, since=cursor) as events:
                    async for event in events:
                        if event.id is not None:
                            cursor = event.id
                        yield event
                        # Progress made — reset the consecutive-failure budget.
                        attempt = 0
                        backoff = initial_backoff
                return
            except (
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.ConnectError,
                httpx.TimeoutException,
            ):
                attempt += 1
                if attempt > max_retries:
                    raise
                await asyncio.sleep(min(backoff, max_backoff))
                backoff *= backoff_multiplier

    async def run(
        self,
        *,
        agent_id: str | UUID,
        prompt: str,
        environment_id: str | UUID | None = None,
        timeout: int | None = None,
        resources: list[dict[str, Any]] | None = None,
        on_event: Callable[[StreamEvent], Awaitable[None] | None] | None = None,
        collect_events: bool = True,
    ) -> RunResult:
        """Create a session, stream until the first turn finishes, and
        return the final session record. See `Sessions.run` for the
        semantics; `on_event` may be sync or async.
        """
        ack = await self.create(
            agent_id=agent_id,
            prompt=prompt,
            environment_id=environment_id,
            timeout=timeout,
            resources=resources,
        )
        return await self.wait_for_completion(
            ack.id, on_event=on_event, collect_events=collect_events
        )

    async def wait_for_completion(
        self,
        session_id: str | UUID,
        *,
        since: int | None = None,
        on_event: Callable[[StreamEvent], Awaitable[None] | None] | None = None,
        collect_events: bool = True,
    ) -> RunResult:
        """See `Sessions.wait_for_completion` — async variant. `on_event`
        may be sync or async.
        """
        events: list[StreamEvent] = []
        async with self.stream(session_id, since=since) as stream:
            async for event in stream:
                if on_event is not None:
                    result = on_event(event)
                    if inspect.isawaitable(result):
                        await result
                if collect_events:
                    events.append(event)
                if event.type in TERMINAL_EVENT_TYPES:
                    break
        session = await self.get(session_id)
        return RunResult(session=session, events=events)
