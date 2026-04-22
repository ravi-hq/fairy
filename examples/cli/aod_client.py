"""Minimal Python client for the Agent on Demand HTTP API.

Stdlib-only (urllib). Sufficient for example CLIs and small scripts; not a
full-featured SDK. Raises `AodError` on any non-2xx response or transport
failure so callers can surface a clean message without a traceback.
"""

from __future__ import annotations

import json
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class AodError(Exception):
    pass


class AodClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._auth = {"Authorization": f"Bearer {token}"}

    def _open(self, method: str, path: str, body: dict | None = None, stream: bool = False):
        headers = dict(self._auth)
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        if stream:
            headers["Accept"] = "text/event-stream"
        req = Request(f"{self.base_url}{path}", method=method, headers=headers, data=data)
        try:
            return urlopen(req)
        except HTTPError as e:
            raise AodError(
                f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')}"
            ) from e
        except URLError as e:
            raise AodError(f"{method} {path}: {e.reason}") from e

    def get(self, path: str) -> dict:
        with self._open("GET", path) as resp:
            return json.loads(resp.read())

    def post(self, path: str, body: dict) -> dict:
        with self._open("POST", path, body) as resp:
            return json.loads(resp.read())

    def ensure(self, resource: str, name: str, create_payload: dict) -> str:
        """Return the id of a non-archived `resource` with the given name,
        creating it from `create_payload` if none exists. `resource` is the
        API path segment, e.g. `"agents"` or `"environments"`."""
        for item in self.get(f"/{resource}")["data"]:
            if item["name"] == name:
                return item["id"]
        return self.post(f"/{resource}", create_payload)["id"]

    def create_session(
        self,
        *,
        agent_id: str,
        prompt: str,
        timeout: int,
        resources: list[dict] | None = None,
        environment_id: str | None = None,
    ) -> dict:
        body: dict = {"agent_id": agent_id, "prompt": prompt, "timeout": timeout}
        if resources:
            body["resources"] = resources
        if environment_id:
            body["environment_id"] = environment_id
        return self.post("/sessions", body)

    def continue_session(self, session_id: str, *, prompt: str, timeout: int) -> dict:
        return self.post(f"/sessions/{session_id}/prompt", {"prompt": prompt, "timeout": timeout})

    def stream_session(self, session_id: str) -> Iterator[dict]:
        """Yield parsed SSE event payloads for a session, one per `data:` line.
        Skips heartbeats, `id:` lines, and blanks. Closes when the stream ends."""
        with self._open("GET", f"/sessions/{session_id}/stream", stream=True) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.startswith("data: "):
                    continue
                yield json.loads(line[6:])
