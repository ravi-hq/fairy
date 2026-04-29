"""E2E test fixtures and helpers.

Required env vars:
    AOD_API_URL   — base URL of the agent-on-demand deployment (default: http://localhost:8000)
    AOD_API_TOKEN — valid API key for a preconfigured user

Optional env vars:
    E2E_RUNTIMES — comma-separated runtimes to test (default: claude)
    E2E_TIMEOUT  — max seconds to wait for a session (default: 180)
"""

import json
import os
import time
import uuid

import pytest
import requests

# Cheapest model per runtime for fast, inexpensive tests
RUNTIME_MODELS = {
    "claude": "anthropic/claude-haiku-4-5",
    "codex": "openai/o4-mini",
    "gemini": "google/gemini-2.5-flash",
    "opencode": "anthropic/claude-haiku-4-5",
}

DEFAULT_TIMEOUT = int(os.environ.get("E2E_TIMEOUT", "180"))


def pytest_collection_modifyitems(config, items):
    """Auto-skip e2e tests when AOD_API_TOKEN is not set."""
    if os.environ.get("AOD_API_TOKEN"):
        return
    skip = pytest.mark.skip(reason="AOD_API_TOKEN not set")
    for item in items:
        if "/e2e/" in str(item.fspath):
            item.add_marker(skip)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class APIClient:
    """Thin HTTP wrapper around the agent-on-demand API."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.http.headers["Content-Type"] = "application/json"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # -- Health ---------------------------------------------------------------
    def health(self):
        return self.http.get(self._url("/health"))

    # -- Agents ---------------------------------------------------------------
    def create_agent(self, **kw):
        return self.http.post(self._url("/agents"), json=kw)

    def get_agent(self, aid):
        return self.http.get(self._url(f"/agents/{aid}"))

    def list_agents(self):
        return self.http.get(self._url("/agents"))

    def update_agent(self, aid, **kw):
        return self.http.put(self._url(f"/agents/{aid}"), json=kw)

    def archive_agent(self, aid):
        return self.http.post(self._url(f"/agents/{aid}/archive"))

    def list_agent_versions(self, aid):
        return self.http.get(self._url(f"/agents/{aid}/versions"))

    # -- Environments ---------------------------------------------------------
    def create_environment(self, **kw):
        return self.http.post(self._url("/environments"), json=kw)

    def get_environment(self, eid):
        return self.http.get(self._url(f"/environments/{eid}"))

    def list_environments(self):
        return self.http.get(self._url("/environments"))

    def update_environment(self, eid, **kw):
        return self.http.put(self._url(f"/environments/{eid}"), json=kw)

    def archive_environment(self, eid):
        return self.http.post(self._url(f"/environments/{eid}/archive"))

    def delete_environment(self, eid):
        return self.http.delete(self._url(f"/environments/{eid}/delete"))

    def list_environment_versions(self, eid):
        return self.http.get(self._url(f"/environments/{eid}/versions"))

    # -- Sessions -------------------------------------------------------------
    def create_session(self, **kw):
        return self.http.post(self._url("/sessions"), json=kw)

    def get_session(self, sid):
        return self.http.get(self._url(f"/sessions/{sid}"))

    def send_prompt(self, sid, **kw):
        return self.http.post(self._url(f"/sessions/{sid}/prompt"), json=kw)

    def interrupt_session(self, sid):
        return self.http.post(self._url(f"/sessions/{sid}/interrupt"))

    def terminate_session(self, sid):
        return self.http.post(self._url(f"/sessions/{sid}/terminate"))

    def delete_session(self, sid):
        return self.http.delete(self._url(f"/sessions/{sid}/delete"))

    def stream_session_raw(self, sid):
        """Return a streaming response for SSE consumption."""
        return self.http.get(self._url(f"/sessions/{sid}/stream"), stream=True)

    # -- Helpers --------------------------------------------------------------
    def wait_for_session(self, sid, timeout=DEFAULT_TIMEOUT, poll=2):
        """Poll GET /sessions/{sid} until a terminal status is reached."""
        deadline = time.time() + timeout
        last_status = "unknown"
        while time.time() < deadline:
            resp = self.get_session(sid)
            resp.raise_for_status()
            last_status = resp.json()["status"]
            if last_status in ("completed", "failed", "terminated"):
                return resp.json()
            time.sleep(poll)
        raise TimeoutError(f"Session {sid} still '{last_status}' after {timeout}s")

    def collect_stream(self, sid, timeout=DEFAULT_TIMEOUT):
        """Consume SSE stream and return a list of parsed event dicts."""
        events: list[dict] = []
        resp = self.stream_session_raw(sid)
        resp.raise_for_status()
        deadline = time.time() + timeout
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if time.time() > deadline:
                    break
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    payload = line[6:]
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        event = {"type": "raw", "data": payload}
                    events.append(event)
                    if event.get("type") in ("exit", "error", "terminated"):
                        break
        finally:
            resp.close()
        return events

    def run_session(self, sid, timeout=DEFAULT_TIMEOUT):
        """Wait for a session to reach a terminal state via the SSE stream.

        Returns (final_status_dict, events). Single network roundtrip — no
        polling — so it eliminates the 0–2s jitter of `wait_for_session`. Use
        this instead of `wait_for_session` + `collect_stream` when the test
        needs both.
        """
        events = self.collect_stream(sid, timeout=timeout)
        resp = self.get_session(sid)
        resp.raise_for_status()
        return resp.json(), events


def stream_stdout(events: list[dict]) -> str:
    """Concatenate all stdout data from stream events."""
    return "".join(
        e.get("data", "")
        for e in events
        if e.get("type") == "output" and e.get("stream") == "stdout"
    )


def stream_all_output(events: list[dict]) -> str:
    """Concatenate all output data (stdout + stderr) from stream events."""
    return "".join(e.get("data", "") for e in events if e.get("type") == "output")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def aod_url():
    return os.environ.get("AOD_API_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def aod_token():
    token = os.environ.get("AOD_API_TOKEN")
    if not token:
        pytest.skip("AOD_API_TOKEN not set")
    return token


@pytest.fixture(scope="session")
def e2e_runtimes():
    raw = os.environ.get("E2E_RUNTIMES", "claude")
    return [r.strip() for r in raw.split(",") if r.strip()]


@pytest.fixture(scope="session")
def e2e_timeout():
    return DEFAULT_TIMEOUT


@pytest.fixture(scope="session")
def api(aod_url, aod_token):
    return APIClient(aod_url, aod_token)


# ---------------------------------------------------------------------------
# Per-test factory fixtures with automatic cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def create_agent(api):
    """Factory: creates agents and archives them after the test."""
    created: list[str] = []

    def _create(**kw):
        resp = api.create_agent(**kw)
        resp.raise_for_status()
        data = resp.json()
        created.append(data["id"])
        return data

    yield _create

    for aid in created:
        try:
            api.archive_agent(aid)
        except Exception:
            pass


@pytest.fixture
def create_environment(api):
    """Factory: creates environments and archives them after the test."""
    created: list[str] = []

    def _create(**kw):
        resp = api.create_environment(**kw)
        resp.raise_for_status()
        data = resp.json()
        created.append(data["id"])
        return data

    yield _create

    for eid in created:
        try:
            api.archive_environment(eid)
        except Exception:
            pass


@pytest.fixture
def create_session(api):
    """Factory: creates sessions, terminates + deletes them after the test."""
    created: list[str] = []

    def _create(**kw):
        resp = api.create_session(**kw)
        resp.raise_for_status()
        data = resp.json()
        created.append(data["id"])
        return data

    yield _create

    for sid in created:
        try:
            # Stream-based wait beats polling and naturally returns instantly
            # for sessions that already completed.
            api.collect_stream(sid, timeout=30)
        except Exception:
            pass
        try:
            api.terminate_session(sid)
        except Exception:
            pass
        try:
            api.delete_session(sid)
        except Exception:
            pass
