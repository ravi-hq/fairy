from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

import httpx
import pytest

from aod import AsyncClient, Client


@dataclass
class RecordedRequest:
    method: str
    path: str
    body: Any
    headers: dict[str, str] = field(default_factory=dict)
    params: dict[str, list[str]] = field(default_factory=dict)


Responder = Callable[[httpx.Request], httpx.Response]


@dataclass
class MockServer:
    """Small helper to register per-route handlers for a mock transport."""

    routes: dict[tuple[str, str], Responder] = field(default_factory=dict)
    requests: list[RecordedRequest] = field(default_factory=list)

    def register(self, method: str, path: str, responder: Responder) -> None:
        self.routes[(method.upper(), path)] = responder

    def json(
        self, method: str, path: str, status: int, body: Any, headers: dict[str, str] | None = None
    ) -> None:
        def responder(_: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json=body, headers=headers or {})

        self.register(method, path, responder)

    def handle(self, request: httpx.Request) -> httpx.Response:
        body: Any
        raw = request.content
        if raw:
            try:
                body = json.loads(raw)
            except json.JSONDecodeError:
                body = raw.decode(errors="replace")
        else:
            body = None
        self.requests.append(
            RecordedRequest(
                method=request.method,
                path=request.url.path,
                body=body,
                headers=dict(request.headers),
                params={k: request.url.params.get_list(k) for k in request.url.params.keys()},
            )
        )
        responder = self.routes.get((request.method, request.url.path))
        if responder is None:
            return httpx.Response(
                501, json={"detail": f"No mock for {request.method} {request.url.path}"}
            )
        return responder(request)


@pytest.fixture
def server() -> MockServer:
    return MockServer()


@pytest.fixture
def client(server: MockServer) -> Client:
    transport = httpx.MockTransport(server.handle)
    return Client(base_url="http://mock", token="aod_test", transport=transport)


@pytest.fixture
def async_client(server: MockServer) -> AsyncClient:
    transport = httpx.MockTransport(server.handle)
    return AsyncClient(base_url="http://mock", token="aod_test", transport=transport)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def make_agent() -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        base = {
            "id": str(uuid4()),
            "type": "agent",
            "name": "demo",
            "description": None,
            "system": None,
            "model": "claude-sonnet-4-5",
            "runtime": "claude-code",
            "environment_id": None,
            "skills": [],
            "mcp_servers": [],
            "metadata": {},
            "version": 1,
            "archived_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        base.update(overrides)
        return base

    return _make


@pytest.fixture
def make_environment() -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        base = {
            "id": str(uuid4()),
            "type": "environment",
            "name": "demo-env",
            "packages": {},
            "setup_script": None,
            "networking": {"type": "unrestricted"},
            "version": 1,
            "archived_at": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        base.update(overrides)
        return base

    return _make


@pytest.fixture
def make_session() -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        base = {
            "id": str(uuid4()),
            "agent_id": str(uuid4()),
            "environment_id": None,
            "runtime": "claude-code",
            "status": "completed",
            "exit_code": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "resources": [],
            "turn_count": 1,
            "current_turn": 1,
        }
        base.update(overrides)
        return base

    return _make
