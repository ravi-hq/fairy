# ASGI-Everywhere Migration Implementation Plan

## Overview

Convert the Fairy Django app from WSGI (gunicorn + gthread) to ASGI (uvicorn) across the whole service — one service, one runtime, one deploy artifact. The goal is to lift the SSE ceiling from ~24 concurrent streams (thread-pinned) to hundreds (coroutine-based) without splitting services or introducing a reverse proxy. Non-SSE views continue to work unchanged through Django's sync-to-async adapter.

This plan supersedes the earlier rung-1 / rung-2 split plan in `thoughts/research/2026-04-20-sse-split-asgi.md`. That research remains valid as context; the user chose ASGI-everywhere over splitting services.

## Research Summary

Research conducted by agent team with 4 specialist tracks:
- **Code conversion**: exact async diffs for `stream.py`, `stream_session` + `event_generator`, and `require_api_key`. Confirmed Django 6.0.4 is installed (not 5.1 as assumed) — async ORM is fully mature. Uses `async for row in qs[:100]` (not `.aiterator()`) for polling fetches.
- **Compatibility audit**: every middleware is async-compatible except `WhiteNoiseMiddleware` (sync, adapted by Django, acceptable overhead). `send_prompt`'s `transaction.atomic()` + `select_for_update()` have no async equivalents in Django — the view must stay sync. UI views stay sync too — Django 6 auto-adapts them.
- **Deploy & dev experience**: exact `render.yaml` / `pyproject.toml` / `Makefile` / `config/asgi.py` diffs. Per user decision, `make dev` also moves to uvicorn for prod/dev parity.
- **Verification & rollback**: smoke test checklist, load test script for rung-2 validation, Honeycomb queries, rollback commit sequence.

### Key Discoveries

- **Django 6.0.4 is installed**, not 5.1 ([pyproject.toml dep]). Async ORM is fully mature; `aget`, `aiterator`, and `__aiter__` on sliced querysets all work correctly.
- **`transaction.atomic()` has no async context manager** (confirmed by reading `.venv/lib/python3.14/site-packages/django/db/transaction.py`). `send_prompt` at [`views/sessions.py:336-434`](src/agent_on_demand/views/sessions.py#L336-L434) must stay sync. Django adapts sync views under ASGI through a threadpool — this is the correct pattern.
- **PostHog middleware is already async-capable** (installed `posthog` package has explicit `async_capable = True` and `__acall__` at `posthog/integrations/django.py:54-55,276-294`). Earlier research in `thoughts/research/2026-04-20-sse-split-asgi.md` was wrong on this point.
- **Use `time.monotonic()` in `stream.py`, not `asyncio.get_event_loop().time()`** — keeps existing test-mocking patterns (`mocker.patch("agent_on_demand.stream.time")`) working with minimal change. Only `time.sleep` becomes `await asyncio.sleep`.
- **`@login_required` in Django 6 inspects `iscoroutinefunction(view_func)` and branches** ([`django/contrib/auth/decorators.py:37-66`]). All UI views are sync, so the sync branch runs — no change needed.
- **`require_api_key` has a dual-mode challenge**: 5 of the 6 views it decorates become `async def`, but `send_prompt` stays sync. The decorator must detect the view type and produce the appropriate wrapper.

## Current State Analysis

- `render.yaml:18-25` runs `gunicorn config.wsgi:application --workers 3 --worker-class gthread --threads 8 ...` — sync WSGI with 24 total thread slots.
- `src/config/wsgi.py` exists; **`src/config/asgi.py` does not**.
- `pyproject.toml` depends on `gunicorn>=23.0`; **no `uvicorn`**; **no `pytest-asyncio`** in dev deps.
- `stream.py` uses `time.sleep(0.5)` in a polling loop — pins a thread for the session duration (up to 600s idle or 3600s active).
- `views/sessions.py::stream_session` is sync; SSE generator is sync.
- `auth.py::require_api_key` is sync.
- `send_prompt` at `views/sessions.py:336-434` uses `transaction.atomic()` + `select_for_update()` for turn-number allocation under concurrent POSTs.
- `tests/test_stream.py` has 4 tests that call `list(stream_session_from_db(...))` directly (the generator, not via HTTP view) and 5 tests that hit the HTTP view via Django's sync `Client`.
- `Makefile:dev` uses `runserver` which is WSGI-only but auto-adapts async views.

## Desired End State

- Web service runs `uvicorn config.asgi:application --workers 3` on Render.
- `stream_session` runs as coroutines; a single uvicorn worker serves hundreds of concurrent SSE streams.
- Every non-stream endpoint continues to work; sync views are auto-adapted via Django's threadpool.
- `make dev` runs uvicorn with `--reload`.
- Unit tests pass under `make test` with pytest-asyncio configured.
- E2E tests pass under `make test-e2e E2E_WORKERS=4` where they currently fail.
- Rung-2 load test confirms 100+ concurrent SSE streams while `/health` P95 stays <100ms.

## What We're NOT Doing

- **Not splitting services.** One uvicorn deployment replaces gunicorn. No separate stream service, no reverse proxy, no new hostname.
- **Not converting `send_prompt` to async.** `transaction.atomic()` doesn't support async; keeping it sync is the safe path.
- **Not converting UI views to async.** They stay sync; Django adapts them.
- **Not adding psycopg connection pooling.** Orthogonal to ASGI migration; revisit separately if connection counts become a problem.
- **Not touching the Procrastinate worker.** Separate process, unchanged.
- **Not implementing `--max-requests` worker recycling.** uvicorn core doesn't have it. Monitor RSS; revisit if memory grows.
- **Not adding a reverse proxy for static files.** WhiteNoise stays. The sync-adapter overhead on static requests is negligible in production (CDN-backed).

## Implementation Approach

Four phases, each independently verifiable and reversible:

1. **Foundation** — dependencies, `config/asgi.py`, `render.yaml`, `Makefile`. No runtime behavior change; Django still runs as-is under uvicorn via its sync adapter.
2. **Code conversion** — convert the three files that actually benefit from async (`stream.py`, `stream_session` view, `require_api_key` decorator).
3. **Tests** — add `pytest-asyncio`, convert the 4 generator-direct tests.
4. **Deploy + verify** — smoke test locally, deploy to Render, run load test, monitor.

Phases 1 and 3 are parallelizable if desired; Phase 2 depends on Phase 1 (needs asgi.py + uvicorn to test).

## File Ownership Map

| File | Phase | Change Type |
|------|-------|-------------|
| `pyproject.toml` | 1 | modify (deps) |
| `src/config/asgi.py` | 1 | create |
| `render.yaml` | 1 | modify |
| `Makefile` | 1 | modify |
| `src/agent_on_demand/stream.py` | 2 | rewrite as async |
| `src/agent_on_demand/views/sessions.py` | 2 | convert `stream_session` + `event_generator` |
| `src/agent_on_demand/auth.py` | 2 | dual-mode decorator |
| `tests/test_stream.py` | 3 | convert 4 generator tests |
| `pyproject.toml` | 3 | add pytest-asyncio config |

All single-owner per phase; no conflicts.

---

## Phase 1: Foundation

### Overview
Add uvicorn, create ASGI entrypoint, swap deploy config. After this phase Django still serves all views synchronously under uvicorn's sync adapter — runtime behavior unchanged.

### Changes Required

#### 1. `pyproject.toml` — dependency swap

Remove `gunicorn`; add `uvicorn[standard]`. (pytest-asyncio is added in Phase 3.)

```diff
 dependencies = [
     ...
-    "gunicorn>=23.0",
+    "uvicorn[standard]>=0.30",
     ...
 ]
```

`uvicorn[standard]` bundles `uvloop`, `httptools`, and `watchfiles` — needed for perf and `--reload`.

#### 2. `src/config/asgi.py` — new file

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_asgi_application()
```

Mirror of `config/wsgi.py`.

#### 3. `render.yaml` — startCommand swap

```diff
-            startCommand: >-
-              uv run gunicorn config.wsgi:application
-              --bind 0.0.0.0:$PORT
-              --workers 3
-              --worker-class gthread
-              --threads 8
-              --graceful-timeout 650
-              --timeout 700
-              --max-requests 1000
-              --max-requests-jitter 50
+            startCommand: >-
+              uv run uvicorn config.asgi:application
+              --host 0.0.0.0
+              --port $PORT
+              --workers 3
+              --timeout-graceful-shutdown 650
```

Flags we lose and why it's OK:
- `--timeout 700` — uvicorn has no per-request kill. For SSE under async, the coroutine yields on every `asyncio.sleep`, so no worker is ever blocked waiting on a stream. Non-SSE views are fast.
- `--max-requests` — worker recycling is gone. Monitor RSS; revisit if needed.

#### 4. `Makefile` — dev target

```diff
 dev:
-	uv run python manage.py runserver 0.0.0.0:8777
+	PYTHONPATH=src uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8777 --reload
```

`PYTHONPATH=src` is needed because uvicorn doesn't go through `manage.py` which normally adds `src/` to the path.

### Success Criteria

#### Automated Verification
- [ ] `uv sync` completes cleanly: `make install`
- [ ] Lint passes: `make lint`
- [ ] Unit tests pass against new deps: `make test`

#### Manual Verification
- [ ] `make dev` starts uvicorn on port 8777, auto-reloads on file change
- [ ] `curl http://localhost:8777/health` returns `{"status":"ok"}`
- [ ] `uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8777` works without `PYTHONPATH=src` from repo root (if `uv run` handles it)

**Gate**: Pause. Do not proceed to Phase 2 until local uvicorn serves the existing sync app correctly.

---

## Phase 2: Code conversion to async

### Overview
Convert exactly three code locations. Every other view remains sync and is adapted by Django. This is the only phase with runtime semantic changes.

### Changes Required

#### 1. `src/agent_on_demand/auth.py` — dual-mode decorator

The decorator must detect whether the wrapped view is sync or async and produce the matching wrapper. This is because 5 of the 6 `@require_api_key` views become async but `send_prompt` stays sync.

```python
import asyncio
import functools

from django.http import JsonResponse
from django.utils import timezone as tz

from agent_on_demand.models import APIKey


def _check_api_key_sync(request):
    """Return (api_key, error_response) — error_response is None on success."""
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None, JsonResponse(
            {"detail": "Missing or invalid Authorization header"}, status=401
        )
    raw_key = auth_header[7:]
    key_hash = APIKey.hash_key(raw_key)
    try:
        api_key = APIKey.objects.select_related("user").get(key_hash=key_hash)
    except APIKey.DoesNotExist:
        return None, JsonResponse({"detail": "Invalid API key"}, status=401)
    if not api_key.is_active:
        return None, JsonResponse({"detail": "API key is inactive"}, status=401)
    if api_key.expires_at and api_key.expires_at <= tz.now():
        return None, JsonResponse({"detail": "API key has expired"}, status=401)
    return api_key, None


async def _check_api_key_async(request):
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None, JsonResponse(
            {"detail": "Missing or invalid Authorization header"}, status=401
        )
    raw_key = auth_header[7:]
    key_hash = APIKey.hash_key(raw_key)
    try:
        api_key = await APIKey.objects.select_related("user").aget(key_hash=key_hash)
    except APIKey.DoesNotExist:
        return None, JsonResponse({"detail": "Invalid API key"}, status=401)
    if not api_key.is_active:
        return None, JsonResponse({"detail": "API key is inactive"}, status=401)
    if api_key.expires_at and api_key.expires_at <= tz.now():
        return None, JsonResponse({"detail": "API key has expired"}, status=401)
    return api_key, None


def require_api_key(view_func):
    """Decorator that authenticates requests via Bearer token.

    Dispatches to async or sync flavor based on the wrapped view's type.
    """
    if asyncio.iscoroutinefunction(view_func):
        @functools.wraps(view_func)
        async def async_wrapper(request, *args, **kwargs):
            api_key, err = await _check_api_key_async(request)
            if err is not None:
                return err
            request.user = api_key.user
            request.api_key_obj = api_key
            return await view_func(request, *args, **kwargs)
        return async_wrapper

    @functools.wraps(view_func)
    def sync_wrapper(request, *args, **kwargs):
        api_key, err = _check_api_key_sync(request)
        if err is not None:
            return err
        request.user = api_key.user
        request.api_key_obj = api_key
        return view_func(request, *args, **kwargs)
    return sync_wrapper
```

#### 2. `src/agent_on_demand/stream.py` — async generator

```python
import asyncio
import json
import time
from collections.abc import AsyncGenerator

from agent_on_demand.models import AgentSession, AgentSessionLog

STREAM_IDLE_LIMIT = 600


def _format(event_type: str, log_id: int, payload: dict) -> str:
    return json.dumps({"type": event_type, "id": log_id, **payload})


async def stream_session_from_db(session_id: str, since: int = 0) -> AsyncGenerator[str, None]:
    """Yield SSE event strings by tailing AgentSessionLog.

    - Starts after `since` (exclusive). 0 = from the beginning.
    - Stale cursors silently resume from the next surviving row.
    - Exits on terminal status with no new rows, or after STREAM_IDLE_LIMIT
      seconds of no chunks.
    """
    last_id = since
    last_turn_id = None
    last_heartbeat = time.monotonic()
    last_chunk_time = time.monotonic()

    while True:
        chunks = [
            row
            async for row in AgentSessionLog.objects.filter(
                session_id=session_id, id__gt=last_id
            )
            .order_by("id")
            .values("id", "stream", "data", "turn_id", "turn__turn_number")[:100]
        ]

        if chunks:
            last_chunk_time = time.monotonic()

        for chunk in chunks:
            last_id = chunk["id"]
            turn_id = chunk["turn_id"]
            if turn_id is not None and turn_id != last_turn_id:
                yield _format("turn_start", chunk["id"], {"turn": chunk["turn__turn_number"]})
                last_turn_id = turn_id
            yield _format(
                "output",
                chunk["id"],
                {
                    "stream": chunk["stream"],
                    "data": chunk["data"],
                    "turn": chunk["turn__turn_number"],
                },
            )

        session = await AgentSession.objects.aget(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield _format("terminated", last_id, {"message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield _format("error", last_id, {"message": "Session failed"})
            else:
                yield _format("exit", last_id, {"code": session.exit_code})
            break

        now = time.monotonic()
        if now - last_chunk_time > STREAM_IDLE_LIMIT:
            yield _format("stale", last_id, {"message": f"No output for {STREAM_IDLE_LIMIT}s"})
            break

        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        await asyncio.sleep(0.5)
```

Keep `import time` and `time.monotonic()` so existing `mocker.patch("agent_on_demand.stream.time")` test patterns keep working.

#### 3. `src/agent_on_demand/views/sessions.py` — async `stream_session`

Replace the function at lines 296–330 with:

```python
@require_GET
@require_api_key
async def stream_session(request, session_id):
    """Stream session logs via SSE (live tail + full replay)."""
    try:
        session = await AgentSession.objects.aget(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("since", "0")
    try:
        since = max(0, int(raw))
    except ValueError:
        return JsonResponse({"detail": "since must be an integer"}, status=400)

    async def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'runtime': session.runtime, 'session_id': str(session.id)})}\n\n"

        async for event in stream_session_from_db(str(session.id), since=since):
            if event == "":
                yield ": heartbeat\n\n"
            else:
                payload = json.loads(event)
                log_id = payload.get("id")
                if log_id:
                    yield f"id: {log_id}\n"
                yield f"data: {event}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
```

No other views in `sessions.py` change.

### Success Criteria

#### Automated Verification
- [ ] Lint passes: `make lint`
- [ ] Unit tests pass: `make test` (Phase 3 updates the relevant tests)
- [ ] No `SynchronousOnlyOperation` warnings in test output

#### Manual Verification
- [ ] Start `make dev`, hit `/health` → OK
- [ ] Create a session via the API, stream it via `curl -N http://localhost:8777/sessions/<id>/stream -H "Authorization: Bearer $TOKEN"` → see `start`, `output`, `exit` events
- [ ] Send a follow-up prompt via `POST /sessions/<id>/prompt` → session runs again (verifies `send_prompt`'s sync transaction still works under ASGI)
- [ ] UI login at `/ui/login` works — session cookie auth flow intact
- [ ] Open two streams for different sessions simultaneously; both emit events independently (proves async yielding)

**Gate**: Do not proceed to Phase 3 until two concurrent local streams work on the same uvicorn worker.

---

## Phase 3: Tests

### Overview
Add `pytest-asyncio` and convert the 4 generator-direct tests in `tests/test_stream.py`. Sync HTTP-view tests remain unchanged.

### Changes Required

#### 1. `pyproject.toml` — add pytest-asyncio

```diff
 [project.optional-dependencies]
 dev = [
     ...
+    "pytest-asyncio>=0.23",
 ]

 [tool.pytest.ini_options]
 ...
+asyncio_mode = "auto"
```

`asyncio_mode = "auto"` avoids having to decorate every async test with `@pytest.mark.asyncio`.

#### 2. `tests/test_stream.py` — convert 4 generator-direct tests

Tests to convert (currently they wrap the sync generator in `list(...)`):

- `test_stream_replays_from_since_cursor` (line ~81)
- `test_stream_full_replay_when_since_zero` (line ~97)
- `test_stream_lenient_on_stale_cursor` (line ~113)
- `test_stream_emits_stale_event_on_idle_timeout` (line ~148)

Pattern for each:

```python
# Before
def test_stream_replays_from_since_cursor(...):
    events = list(stream_session_from_db(session_id, since=5))
    ...

# After
async def test_stream_replays_from_since_cursor(...):
    events = [e async for e in stream_session_from_db(session_id, since=5)]
    ...
```

For `test_stream_emits_stale_event_on_idle_timeout`, also patch `asyncio.sleep` as a no-op so the test doesn't actually wait:

```python
import asyncio
from unittest.mock import AsyncMock

async def test_stream_emits_stale_event_on_idle_timeout(mocker, ...):
    mocker.patch("agent_on_demand.stream.asyncio.sleep", new=AsyncMock(return_value=None))
    # existing mock_time.monotonic pattern still works because we kept `import time` + `time.monotonic()`
    mock_time = mocker.patch("agent_on_demand.stream.time")
    mock_time.monotonic.side_effect = [0, 0, 601, 601]  # advances past STREAM_IDLE_LIMIT
    events = [e async for e in stream_session_from_db(session_id)]
    ...
```

The 5 view-level tests (`test_stream_emits_id_field_via_view`, `test_stream_view_parses_last_event_id_header`, etc.) use the Django sync `Client` and require no changes.

### Success Criteria

#### Automated Verification
- [ ] `make test` passes all unit tests
- [ ] Specifically: `DATABASE_URL=sqlite:///test.db uv run pytest tests/test_stream.py -v` passes all 9 tests
- [ ] No `DeprecationWarning: coroutine '...' was never awaited` in test output

#### Manual Verification
- [ ] None — fully automated

---

## Phase 4: Deploy + verify

### Overview
Deploy to Render, run the rung-2 load test, monitor for the first hour.

### Changes Required

No code. This phase is operational.

#### 1. Create branch + commit — keep each phase as a separate commit

```bash
git checkout -b perf/asgi-everywhere
# Phase 1 commit: deps + asgi.py + render.yaml + Makefile
# Phase 2 commit: auth.py + stream.py + views/sessions.py
# Phase 3 commit: pytest-asyncio + test_stream.py
```

This makes rollback granular.

#### 2. Local smoke checklist (pre-push)

With `make dev` running locally on :8777 and a valid API token in `$TOKEN`:

```bash
# Health
curl -sS http://localhost:8777/health

# Agents CRUD
AID=$(curl -sS -X POST http://localhost:8777/agents -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"name":"smoke","model":"claude-haiku-4-5","runtime":"claude"}' | jq -r .id)
curl -sS http://localhost:8777/agents -H "Authorization: Bearer $TOKEN" | jq '.data | length'
curl -sS -X PUT http://localhost:8777/agents/$AID -H "Authorization: Bearer $TOKEN" -d '{"name":"smoke2","version":1}' | jq .version
curl -sS -X POST http://localhost:8777/agents/$AID/archive -H "Authorization: Bearer $TOKEN"

# Session + stream
SID=$(curl -sS -X POST http://localhost:8777/sessions -H "Authorization: Bearer $TOKEN" -d "{\"agent_id\":\"$AID\",\"prompt\":\"say hi\"}" | jq -r .id)
curl -N http://localhost:8777/sessions/$SID/stream -H "Authorization: Bearer $TOKEN"  # should emit events

# Send follow-up (exercises sync send_prompt under ASGI)
curl -sS -X POST http://localhost:8777/sessions/$SID/prompt -H "Authorization: Bearer $TOKEN" -d '{"prompt":"follow up"}'

# Terminate + delete
curl -sS -X POST http://localhost:8777/sessions/$SID/terminate -H "Authorization: Bearer $TOKEN"
curl -sS -X DELETE http://localhost:8777/sessions/$SID/delete -H "Authorization: Bearer $TOKEN"

# Auth failures
curl -sS http://localhost:8777/agents  # expect 401
curl -sS http://localhost:8777/sessions/$SID/stream  # expect 401
```

Also manually in a browser:
- `/ui/login`, log in, see dashboard at `/ui/`
- Click a session, see the session detail page render

#### 3. E2E regression

```bash
make test-e2e-fast  # smoke
make test-e2e E2E_WORKERS=2  # should pass (baseline)
make test-e2e E2E_WORKERS=4  # should NOW PASS (the entire point of this project)
```

If `-n 4` still fails, investigate before deploying — the fix might not be what we thought.

#### 4. Rung-2 load test

`tests/load/test_stream_concurrency.py` (new file):

```python
#!/usr/bin/env python3
"""Rung-2 load test: N concurrent SSE streams against the live service."""
import asyncio, os, time, statistics
import httpx

BASE  = os.environ["AOD_API_URL"]
TOKEN = os.environ["AOD_API_TOKEN"]
SIDS  = os.environ["SESSION_IDS"].split(",")  # pre-created completed sessions
N     = int(os.environ.get("N_STREAMS", "100"))
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


async def stream_one(client, sid):
    t0 = time.monotonic()
    events = 0
    async with client.stream("GET", f"{BASE}/sessions/{sid}/stream", headers=HEADERS) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                events += 1
                if '"type": "exit"' in line or '"type": "terminated"' in line:
                    break
    return time.monotonic() - t0, events


async def poll_health(client, samples):
    for _ in range(20):
        await asyncio.sleep(2)
        t0 = time.monotonic()
        r = await client.get(f"{BASE}/health", headers=HEADERS)
        r.raise_for_status()
        samples.append((time.monotonic() - t0) * 1000)


async def main():
    sids = (SIDS * (N // len(SIDS) + 1))[:N]
    health = []
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.get(f"{BASE}/health", headers=HEADERS)
        print(f"Pre-load /health: {r.status_code}")
        await asyncio.gather(
            poll_health(client, health),
            *[stream_one(client, sid) for sid in sids],
        )
    print(f"Streams completed: {N}")
    print(f"/health under load: p50={statistics.median(health):.1f}ms  "
          f"p95={sorted(health)[int(len(health)*0.95)]:.1f}ms  max={max(health):.1f}ms")
    assert max(health) < 500, f"FAIL: /health max {max(health):.0f}ms > 500ms"
    print("PASS")


asyncio.run(main())
```

Run after creating ~10 completed sessions (any existing completed session works — SSE on a completed session replays from id=0 and exits):

```bash
export AOD_API_URL=https://aod.ravi.id
export AOD_API_TOKEN=...
export SESSION_IDS=<uuid>,<uuid>,<uuid>,...  # ~10 ids
export N_STREAMS=100
uv run python tests/load/test_stream_concurrency.py
```

Expected: all 100 streams complete; `/health` p95 < 100ms; no 502s.

#### 5. Post-deploy monitoring (first 60 minutes)

Honeycomb queries (`service.name = "aod-web"`):
- 5xx rate: `http.status_code >= 500 | GROUP BY http.route | COUNT` — rollback if >1% sustained 5 min
- `/health` P99: `http.route = "/health" | P99(duration_ms)` — rollback if >500ms
- Stream latency: `http.route = "/sessions/{session_id}/stream" | P95(duration_ms)`
- Error logs: `error = true` — investigate any spike

Render metrics:
- CPU utilization (expect ~5-15% idle, <50% under load)
- Memory RSS (watch for growth without `--max-requests` recycling)
- Instance restarts (any in first 30 min → investigate)

Render logs (`render logs agent-on-demand-api --tail`):
- `SynchronousOnlyOperation` — indicates a missed sync call in an async path
- `RuntimeError: no running event loop` — ASGI misconfiguration
- `psycopg.OperationalError: connection pool exhausted` — executor saturation

### Success Criteria

#### Automated Verification
- [ ] `make test-e2e E2E_WORKERS=4` passes
- [ ] Load test script passes with N_STREAMS=100

#### Manual Verification
- [ ] All smoke checklist items return expected status
- [ ] UI login + dashboard + session detail render in browser
- [ ] No 5xx in first hour post-deploy
- [ ] `/health` p99 stays <100ms during load test
- [ ] No `SynchronousOnlyOperation` in Render logs

## Rollback Plan

If the deploy goes wrong:

**Fast path (Render UI rollback)**: Render dashboard → `agent-on-demand-api` → Deploys → select previous successful deploy → Redeploy. Takes ~2 min. No code changes required.

**Git revert path (if code needs to come out)**:
```bash
git log --oneline perf/asgi-everywhere  # find the merge commit MERGE_SHA
git revert -m 1 MERGE_SHA
git push origin main
```
Render auto-deploys the revert.

**Granular revert** (if only one phase is problematic): revert the specific Phase 2/3 commits individually; the feature branch is structured to make each phase an independently-revertible commit.

**In-flight SSE during rollback**: uvicorn's `--timeout-graceful-shutdown 650` gives active streams up to ~10 min to finish during the SIGTERM wind-down. Clients with SSE reconnect + `Last-Event-ID` resume seamlessly.

**Rollback triggers** (hard):
- 5xx rate >5% for 2 consecutive minutes
- `/health` P99 >500ms sustained
- Unhandled async exceptions in Honeycomb error events
- Instance restart loop

## Testing Strategy

### Automated
- Unit tests exercise the async generator directly (`test_stream.py` Phase 3 updates) and via the Django sync test client (unchanged).
- E2E tests hit the running deployment via `requests` — fully compatible with uvicorn serving ASGI.
- Rung-2 load test validates the actual scaling claim.

### Manual
- Local smoke checklist (Phase 4 step 2) before deploy.
- Browser verification of UI flows (login + dashboard + session detail).
- First-hour monitoring after deploy.

## Performance Considerations

- **Expected SSE ceiling increase**: from ~24 concurrent streams (WSGI gthread-pinned) to hundreds (ASGI coroutine + `sync_to_async` ORM borrow pattern). Primary new bottleneck at scale is the async ORM's threadpool executor (default 40 threads).
- **Non-SSE view performance**: unchanged to slightly slower. Sync views incur a single threadpool hop per request (~0.1ms). Negligible.
- **Static file serving** via WhiteNoise: sync-adapted. Adds <1ms per request. Negligible because static files go through Render's edge cache anyway.
- **Postgres connection count**: `conn_max_age=600` + 3 uvicorn workers + ORM threadpool = similar connection profile to current gthread config. No change expected.
- **Memory**: no `--max-requests` recycling, so RSS may grow slowly. Monitor; add periodic restart via a cron or supervisor only if measured growth warrants it.

## References

- Research: `thoughts/research/2026-04-20-sse-split-asgi.md` (prior rung-1/rung-2 analysis — context, not the current plan)
- Research: `thoughts/research/2026-04-19-streaming-high-volume-readiness.md` (prior scaling research identifying the SSE ceiling problem)
- Prior architecture decision: `thoughts/research/2026-04-19-threading-architecture-decision.md` (rejected ASGI for the *whole* service at that time — context has shifted; splitting services was rejected in favor of this unified approach)
- Django 6 async ORM docs: `aget`, `aiterator`, `__aiter__` on sliced QuerySets
- Code hot spots:
  - `src/agent_on_demand/stream.py` — rewritten in Phase 2
  - `src/agent_on_demand/views/sessions.py:296-330` — `stream_session` converted
  - `src/agent_on_demand/views/sessions.py:336-434` — `send_prompt` stays sync
  - `src/agent_on_demand/auth.py` — dual-mode decorator
  - `render.yaml:18-25` — startCommand swap
