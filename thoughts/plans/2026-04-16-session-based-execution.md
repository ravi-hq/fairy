# Session-Based Execution with Stored Logs — Implementation Plan

## Overview

Restructure the fairy API so that `POST /run` no longer streams results inline. Instead, it creates a session, starts execution in a background thread, and returns the session ID. A separate `GET /sessions/{id}/stream` endpoint serves the logs via SSE — either live-tailing during execution, or replaying from the database after the fact. All logs are stored in the database, associated with the session.

## Research Summary

Research conducted by 3-agent team (see `thoughts/research/2026-04-16-session-based-execution.md`):
- **Sprites sessions**: No server-side output storage or replay. `attach_session` streams from attach point forward only. `max_run_after_disconnect` not exposed in sprites-py. Fairy must capture logs itself.
- **DB log patterns**: Individual rows per chunk (not blob) with auto-increment PK as cursor. Buffered `bulk_create` for writes. SQLite WAL mode required for concurrent read+write.
- **Django streaming**: `StreamingHttpResponse` with sync generators works under WSGI. No ASGI or new dependencies needed. "Replay then poll" pattern is straightforward.

### Key Discoveries:
- `QueueWriter` in `stream.py:13-28` is already the interception point for all output — dual-writing to DB requires minimal change
- SQLite is currently in default DELETE journal mode (`settings.py:54-59`) which blocks readers during writes — WAL mode is a prerequisite
- `stream.py:48-49` uses `QueueWriter` for both stdout and stderr with no distinction — need to tag the stream name

## Current State Analysis

**Current flow** (`POST /run`):
1. Validate request → create Sprite → write wrapper script → stream SSE inline → delete Sprite
2. The caller must stay connected for the entire execution. If they disconnect, output is lost.
3. No session model, no log persistence, no way to retrieve results later.

**Existing models**: `APIKey` (hashed), `UserRuntimeKey` (encrypted). No session or log models.

**Server**: Django 6.0.4, WSGI, SQLite, dev server on port 8777.

## Desired End State

```bash
# Start a session — returns immediately
curl -X POST http://localhost:8777/run \
  -H "Content-Type: application/json" \
  -d '{"runtime": "claude", "prompt": "write hello world", "api_key": "sk-ant-..."}'
# → 202 {"id": "abc-123-...", "status": "pending", "stream_url": "/sessions/abc-123-.../stream"}

# Check status
curl http://localhost:8777/sessions/abc-123-.../
# → {"id": "abc-123-...", "runtime": "claude", "status": "running", "created_at": "..."}

# Stream logs (works during execution AND after completion)
curl -N http://localhost:8777/sessions/abc-123-.../stream
# → data: {"type": "output", "stream": "stdout", "data": "..."}
# → data: {"type": "output", "stream": "stdout", "data": "..."}
# → data: {"type": "exit", "code": 0}
```

Verify by:
1. `POST /run` returns 202 with a session UUID
2. `GET /sessions/{id}` returns session metadata with status lifecycle
3. `GET /sessions/{id}/stream` replays all stored logs and live-tails new ones
4. After session completes, `GET /sessions/{id}/stream` replays everything from DB
5. Sprite is cleaned up after execution finishes (not when client disconnects)

## What We're NOT Doing

- No authentication on the new endpoints (existing issue, tracked separately)
- No Celery or task queue — background threads are sufficient for v1
- No ASGI migration — WSGI handles this fine
- No log retention/cleanup policy — table will grow but SQLite handles it for now
- No WebSocket endpoint for bidirectional streaming
- No pagination on the log stream endpoint (SSE streams everything)
- No multi-user session isolation (no auth = no user scoping)

## Implementation Approach

The key insight: the existing `QueueWriter` pattern in `stream.py` already intercepts every output chunk in a background thread. We modify this to dual-write: each chunk goes to both an in-memory queue (for any connected SSE client) AND the database. The `POST /run` endpoint creates the session row, starts the background thread, and returns immediately. A new `GET /sessions/{id}/stream` endpoint replays from the database and then polls for new rows until the session completes.

## File Ownership Map

Single track (backend). All phases are sequential.

| File | Phase | Change Type |
|------|-------|-------------|
| `src/fairy/models.py` | 1 | modify |
| `src/config/settings.py` | 1 | modify |
| `src/fairy/migrations/0002_agent_sessions.py` | 1 | create (generated) |
| `src/fairy/stream.py` | 2 | modify |
| `src/fairy/views.py` | 3 | modify |
| `src/fairy/urls.py` | 3 | modify |
| `src/fairy/admin.py` | 4 | modify |
| `tests/test_api.py` | 4 | modify |

---

## Phase 1: Models & Database

### Overview
Add `AgentSession` and `AgentSessionLog` models, enable SQLite WAL mode.

### Changes Required:

#### 1. `src/fairy/models.py` — Add session models

Add after the existing `UserRuntimeKey` class:

```python
import uuid

class AgentSession(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    runtime = models.CharField(max_length=32)
    prompt = models.TextField()
    sprite_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_sessions"

    def __str__(self):
        return f"{self.runtime} — {self.status} ({self.id})"


class AgentSessionLog(models.Model):
    STREAM_CHOICES = [
        ("stdout", "stdout"),
        ("stderr", "stderr"),
    ]

    session = models.ForeignKey(
        AgentSession, on_delete=models.CASCADE, related_name="logs"
    )
    stream = models.CharField(max_length=6, choices=STREAM_CHOICES)
    data = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_session_logs"
        indexes = [
            models.Index(fields=["session", "id"]),
        ]

    def __str__(self):
        return f"[{self.stream}] {self.data[:80]}"
```

**Design notes**:
- No `user` FK — auth isn't wired yet. Add when auth lands.
- UUID PK on `AgentSession` — safe to expose in URLs, no enumeration risk.
- `prompt` stored on session — useful for debugging/admin, and means the stream endpoint doesn't need to know the prompt.
- `sprite_name` stored — useful for debugging if Sprites aren't cleaned up.
- Composite index `(session, id)` on `AgentSessionLog` — makes the tailing query `O(log n)`.

#### 2. `src/config/settings.py` — Enable SQLite WAL mode

Change the `DATABASES` block:

```python
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "fairy.db",
        "OPTIONS": {
            "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
        },
    }
}
```

**Why**: Default DELETE journal mode blocks all readers while any write is in progress. The background thread writing log chunks would stall the streaming endpoint. WAL mode allows concurrent reads during writes. `synchronous=NORMAL` is safe with WAL and improves write throughput.

**Note**: Django's SQLite backend doesn't support `init_command` directly. The correct approach is to use a signal:

```python
# At the bottom of settings.py or in fairy/apps.py
from django.db.backends.signals import connection_created

def _set_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")

connection_created.connect(_set_sqlite_pragmas)
```

Place this in `src/fairy/apps.py` inside the `FairyConfig.ready()` method.

#### 3. Generate migration

```bash
uv run python manage.py makemigrations fairy
```

This should generate `0002_agent_sessions.py` with the two new models.

### Success Criteria:

#### Automated Verification:
- [ ] `uv run python manage.py makemigrations --check` reports no pending migrations
- [ ] `uv run python manage.py migrate` succeeds
- [ ] `uv run ruff check src/` passes
- [ ] `uv run pytest tests/ -v` — existing tests still pass

#### Manual Verification:
- [ ] `uv run python manage.py shell -c "from fairy.models import AgentSession, AgentSessionLog; print('OK')"` works
- [ ] SQLite WAL mode confirmed: `uv run python manage.py shell -c "from django.db import connection; c = connection.cursor(); c.execute('PRAGMA journal_mode'); print(c.fetchone())"` prints `('wal',)`

**Gate**: Verify migration applies cleanly before proceeding to Phase 2.

---

## Phase 2: Dual-Write Streaming

### Overview
Modify `stream.py` so that each output chunk is written to both the in-memory queue (for SSE) and the database (for later retrieval). Add stream name tagging to distinguish stdout from stderr.

### Changes Required:

#### 1. `src/fairy/stream.py` — Full rewrite

```python
import io
import json
import queue
import threading
import time
from collections.abc import Generator

from sprites import ExecError, Sprite

from fairy.models import AgentSession, AgentSessionLog

_SENTINEL = object()


class TaggedChunk:
    """A chunk of output tagged with its stream name."""
    __slots__ = ("stream", "data")

    def __init__(self, stream: str, data: bytes):
        self.stream = stream
        self.data = data


class TaggingQueueWriter(io.RawIOBase):
    """A writable BinaryIO that puts tagged chunks into a queue.

    Each chunk is tagged with the stream name (stdout/stderr) so downstream
    consumers can distinguish them.
    """

    def __init__(self, q: queue.Queue, stream: str):
        self._queue = q
        self._stream = stream

    def writable(self) -> bool:
        return True

    def write(self, b: bytes | bytearray) -> int:
        data = bytes(b)
        self._queue.put(TaggedChunk(self._stream, data))
        return len(data)


def run_session_background(
    session: AgentSession,
    sprite: Sprite,
    timeout: float,
    cleanup_fn,
):
    """Run agent in a background thread, writing output to the database.

    This is the "fire and forget" path — POST /run starts this thread and
    returns immediately. Output is persisted to AgentSessionLog rows.
    """
    output_q: queue.Queue = queue.Queue(maxsize=4096)
    db_buffer: list[AgentSessionLog] = []
    FLUSH_SIZE = 20

    def _flush_buffer():
        if db_buffer:
            AgentSessionLog.objects.bulk_create(db_buffer)
            db_buffer.clear()

    def _run_command():
        try:
            cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)
            cmd.stdout = TaggingQueueWriter(output_q, "stdout")
            cmd.stderr = TaggingQueueWriter(output_q, "stderr")
            cmd.run()
            return ("exit", 0)
        except ExecError as e:
            return ("exit", e.exit_code)
        except Exception as e:
            return ("error", str(e))
        finally:
            output_q.put(_SENTINEL)

    # Update session status
    session.status = "running"
    session.save(update_fields=["status", "updated_at"])

    # Run the command in a sub-thread so we can drain the queue in this thread
    cmd_thread = threading.Thread(target=lambda: result_holder.append(_run_command()), daemon=True)
    result_holder: list = []
    cmd_thread.start()

    # Drain queue → DB
    while True:
        try:
            chunk = output_q.get(timeout=1.0)
        except queue.Empty:
            _flush_buffer()
            continue

        if chunk is _SENTINEL:
            break

        db_buffer.append(
            AgentSessionLog(
                session=session,
                stream=chunk.stream,
                data=chunk.data.decode("utf-8", errors="replace"),
            )
        )
        if len(db_buffer) >= FLUSH_SIZE:
            _flush_buffer()

    # Flush remaining
    _flush_buffer()

    cmd_thread.join(timeout=5.0)

    # Update session with result
    if result_holder:
        kind, value = result_holder[0]
        if kind == "exit":
            session.status = "completed" if value == 0 else "failed"
            session.exit_code = value
        else:
            session.status = "failed"
    else:
        session.status = "failed"

    session.save(update_fields=["status", "exit_code", "updated_at"])

    # Cleanup sprite
    cleanup_fn()


def stream_session_from_db(session_id: str) -> Generator[str, None, None]:
    """Yield SSE event strings by tailing the AgentSessionLog table.

    1. Replay all existing rows
    2. Poll for new rows every 500ms
    3. Send heartbeat every 15s
    4. Stop when session is complete/failed AND no new rows remain
    """
    last_id = 0
    last_heartbeat = time.time()

    while True:
        # Fetch new chunks
        chunks = list(
            AgentSessionLog.objects.filter(
                session_id=session_id, id__gt=last_id
            )
            .order_by("id")
            .values("id", "stream", "data")[:100]
        )

        for chunk in chunks:
            last_id = chunk["id"]
            yield json.dumps({
                "type": "output",
                "stream": chunk["stream"],
                "data": chunk["data"],
            })

        # Check if session is done
        session = AgentSession.objects.get(pk=session_id)
        if session.status in ("completed", "failed") and not chunks:
            # Session done and we've drained all logs
            exit_event = {"type": "exit", "code": session.exit_code}
            if session.status == "failed" and session.exit_code is None:
                exit_event = {"type": "error", "message": "Session failed"}
            yield json.dumps(exit_event)
            break

        # Heartbeat to keep connection alive
        now = time.time()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""  # empty string → ": heartbeat\n\n" in SSE framing

        time.sleep(0.5)
```

**Design notes**:
- `TaggedChunk` replaces the raw `bytes` in the queue, carrying the stream name.
- `run_session_background` is the new entry point. It runs entirely in a background thread — no HTTP connection dependency.
- `FLUSH_SIZE = 20` batches DB writes. Balances latency (max 20 chunks delayed) vs efficiency (1 INSERT per 20 chunks).
- `stream_session_from_db` is the new streaming endpoint's generator. It works identically whether called during or after execution.
- Two-condition termination: session status is terminal AND no new chunks in this poll. Prevents the race where status flips to "completed" before the final `_flush_buffer()` commits.

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/fairy/stream.py` passes
- [ ] `uv run pytest tests/ -v` — existing tests still pass (they don't directly test stream.py)

**Gate**: Phase 2 introduces the core logic but doesn't wire it to views yet. Verify the module imports cleanly: `uv run python -c "from fairy.stream import run_session_background, stream_session_from_db"`

---

## Phase 3: New Endpoints

### Overview
Refactor `views.py` to split `POST /run` into three endpoints: create session, get session, and stream session. Update URL routing.

### Changes Required:

#### 1. `src/fairy/views.py` — Refactor

```python
import json
import logging
import threading
import uuid

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from pydantic import BaseModel, Field, ValidationError
from sprites import SpritesClient, SpriteError

from fairy.models import AgentSession
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import build_wrapper_script
from fairy.stream import run_session_background, stream_session_from_db

logger = logging.getLogger(__name__)


def _get_client() -> SpritesClient:
    from django.conf import settings
    return SpritesClient(
        token=settings.SPRITES_TOKEN,
        base_url=settings.SPRITES_BASE_URL,
    )


class RunRequest(BaseModel):
    runtime: str = Field(description="AI runtime: claude, codex, or gemini")
    prompt: str = Field(description="The prompt to send to the agent")
    api_key: str = Field(description="API key for the chosen runtime")
    timeout: int = Field(default=600, ge=10, le=3600, description="Max seconds")


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})


@csrf_exempt
@require_POST
def run_agent(request):
    """Create a session, start execution in background, return session info."""
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"detail": "Invalid JSON"}, status=400)

    try:
        req = RunRequest(**body)
    except ValidationError as e:
        return JsonResponse({"detail": e.errors()}, status=422)

    if req.runtime not in RUNTIMES:
        return JsonResponse(
            {"detail": f"Unknown runtime: {req.runtime}. Must be one of: {list(RUNTIMES)}"},
            status=400,
        )

    from django.conf import settings
    config = RUNTIMES[req.runtime]
    name = f"{settings.SPRITE_NAME_PREFIX}-{uuid.uuid4().hex[:12]}"
    client = _get_client()

    # Create Sprite
    try:
        sprite = client.create_sprite(name)
    except SpriteError as e:
        return JsonResponse({"detail": f"Failed to create Sprite: {e}"}, status=502)

    # Write wrapper script
    try:
        fs = sprite.filesystem()
        script = build_wrapper_script(config, req.api_key, req.prompt)
        (fs / "run-agent.sh").write_text(script)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
    except SpriteError as e:
        _cleanup(client, name)
        return JsonResponse({"detail": f"Failed to prepare Sprite: {e}"}, status=502)

    # Create session record
    session = AgentSession.objects.create(
        runtime=req.runtime,
        prompt=req.prompt,
        sprite_name=name,
        status="pending",
    )

    # Start background execution
    def cleanup():
        _cleanup(client, name)

    thread = threading.Thread(
        target=run_session_background,
        args=(session, sprite, float(req.timeout), cleanup),
        daemon=True,
    )
    thread.start()

    return JsonResponse(
        {
            "id": str(session.id),
            "status": "pending",
            "stream_url": f"/sessions/{session.id}/stream",
        },
        status=202,
    )


@require_GET
def get_session(request, session_id):
    """Return session metadata."""
    try:
        session = AgentSession.objects.get(pk=session_id)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    return JsonResponse({
        "id": str(session.id),
        "runtime": session.runtime,
        "status": session.status,
        "exit_code": session.exit_code,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    })


@require_GET
def stream_session(request, session_id):
    """Stream session logs via SSE.

    Works during execution (live tail) and after completion (full replay).
    """
    try:
        session = AgentSession.objects.get(pk=session_id)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'runtime': session.runtime, 'session_id': str(session.id)})}\n\n"

        for event in stream_session_from_db(str(session.id)):
            if event == "":
                # Heartbeat
                yield ": heartbeat\n\n"
            else:
                yield f"data: {event}\n\n"

    response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


def _cleanup(client: SpritesClient, sprite_name: str):
    try:
        client.delete_sprite(sprite_name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", sprite_name, exc_info=True)
```

**Key changes from current views.py**:
- `run_agent` no longer returns `StreamingHttpResponse`. Returns `JsonResponse` with 202 status.
- Background thread handles the entire Sprite lifecycle (run + cleanup). Client disconnect doesn't affect execution.
- New `get_session` view for metadata polling.
- New `stream_session` view that wraps `stream_session_from_db` in SSE framing.
- `_cleanup` unchanged.

#### 2. `src/fairy/urls.py` — Add new routes

```python
from django.urls import path

from fairy import views

urlpatterns = [
    path("health", views.health),
    path("run", views.run_agent),
    path("sessions/<uuid:session_id>", views.get_session),
    path("sessions/<uuid:session_id>/stream", views.stream_session),
]
```

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/` passes
- [ ] `uv run pytest tests/ -v` — existing validation tests still pass (they test 400/422 responses from `POST /run`, which haven't changed)

#### Manual Verification:
- [ ] `make dev` starts server on 8777
- [ ] `curl -X POST http://localhost:8777/run -H 'Content-Type: application/json' -d '{"runtime":"claude","prompt":"say hello","api_key":"sk-ant-..."}'` returns 202 with session ID
- [ ] `curl http://localhost:8777/sessions/{id}` returns session metadata with status progression
- [ ] `curl -N http://localhost:8777/sessions/{id}/stream` streams log events and closes on completion
- [ ] After session completes, `curl -N http://localhost:8777/sessions/{id}/stream` replays all logs from DB
- [ ] Sprite is deleted after execution completes (check via `sprites list`)

**Gate**: Full end-to-end manual test before proceeding to Phase 4.

---

## Phase 4: Admin & Tests

### Overview
Register new models in Django admin and update tests.

### Changes Required:

#### 1. `src/fairy/admin.py` — Register session models

Add after the existing `UserRuntimeKeyAdmin`:

```python
from fairy.models import AgentSession, AgentSessionLog


class AgentSessionLogInline(admin.TabularInline):
    model = AgentSessionLog
    extra = 0
    fields = ("stream", "data", "created_at")
    readonly_fields = ("stream", "data", "created_at")


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "runtime", "status", "exit_code", "created_at")
    list_filter = ("runtime", "status")
    search_fields = ("id", "sprite_name")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [AgentSessionLogInline]


@admin.register(AgentSessionLog)
class AgentSessionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "stream", "created_at")
    list_filter = ("stream",)
    readonly_fields = ("session", "stream", "data", "created_at")
```

#### 2. `tests/test_api.py` — Update and add tests

The existing tests for `POST /run` validation (invalid JSON, missing fields, invalid runtime, timeout bounds) should still pass — those error paths return before any Sprite interaction. But the test for invalid runtime currently checks `status=400` — that's unchanged.

Add new tests:

```python
@pytest.mark.django_db
def test_run_returns_202_with_session_id(client: Client, mocker):
    """POST /run returns 202 with session info (mock Sprites)."""
    # Mock the Sprites client to avoid real API calls
    mock_client = mocker.patch("fairy.views._get_client")
    mock_sprite = mock_client.return_value.create_sprite.return_value
    mock_sprite.filesystem.return_value.__truediv__ = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    # Mock the background thread to not actually run
    mocker.patch("fairy.views.threading.Thread")

    resp = client.post(
        "/run",
        data=json.dumps({"runtime": "claude", "prompt": "hello", "api_key": "fake"}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert "stream_url" in data


@pytest.mark.django_db
def test_get_session(client: Client):
    from fairy.models import AgentSession
    session = AgentSession.objects.create(
        runtime="claude", prompt="test", status="running"
    )
    resp = client.get(f"/sessions/{session.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(session.id)
    assert data["runtime"] == "claude"
    assert data["status"] == "running"


@pytest.mark.django_db
def test_get_session_not_found(client: Client):
    import uuid
    resp = client.get(f"/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_stream_session_replays_completed(client: Client):
    """Stream endpoint replays logs from a completed session."""
    from fairy.models import AgentSession, AgentSessionLog

    session = AgentSession.objects.create(
        runtime="claude", prompt="test", status="completed", exit_code=0
    )
    AgentSessionLog.objects.create(session=session, stream="stdout", data="hello world")
    AgentSessionLog.objects.create(session=session, stream="stderr", data="warning")

    resp = client.get(f"/sessions/{session.id}/stream")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"

    # Collect SSE events
    content = b"".join(resp.streaming_content).decode()
    assert '"type": "start"' in content
    assert "hello world" in content
    assert "warning" in content
    assert '"type": "exit"' in content
```

**Note**: The existing test `test_run_invalid_runtime` currently expects `status=400` — this is unchanged since the validation happens before any Sprite/session creation. The test `test_run_missing_fields` expects `status=422` — also unchanged.

Add `pytest-mock` to dev dependencies in `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-django>=4.8.0",
    "pytest-mock>=3.14.0",
    "ruff>=0.8.0",
]
```

### Success Criteria:

#### Automated Verification:
- [ ] `uv run ruff check src/ tests/` passes
- [ ] `uv run pytest tests/ -v` — all tests pass (old + new)

#### Manual Verification:
- [ ] Django admin at `/admin/` shows AgentSession and AgentSessionLog models
- [ ] Session detail page in admin shows inline log entries
- [ ] After running a session via API, it appears in admin with logs

---

## Testing Strategy

### Automated:
- Request validation (invalid JSON, missing fields, bad runtime, timeout bounds) — existing tests, unchanged
- Session creation returns 202 with correct shape — new, mocked Sprites
- Session metadata endpoint — new, no mocks needed (pure DB)
- Stream replay from completed session — new, no mocks needed (pure DB)

### Manual Testing Steps:
1. Start server with `SPRITES_TOKEN=... make dev`
2. Create a session: `curl -X POST localhost:8777/run -H 'Content-Type: application/json' -d '{"runtime":"claude","prompt":"write a hello world python script","api_key":"..."}'`
3. Note the session ID from the 202 response
4. Poll status: `curl localhost:8777/sessions/{id}` — verify it transitions from pending → running → completed
5. Stream live: While session is running, `curl -N localhost:8777/sessions/{id}/stream` — verify events arrive in real-time
6. Stream replay: After session completes, `curl -N localhost:8777/sessions/{id}/stream` — verify all events replay and stream closes
7. Verify Sprite cleanup: `sprites list` should not show the fairy-* Sprite after completion
8. Test failure case: Use an invalid API key, verify session status becomes "failed"
9. Test disconnect resilience: Start a session, don't stream it, wait for completion, then stream — all logs should be there

## Performance Considerations

- **SQLite WAL mode**: Required for concurrent read+write. Without it, the streaming endpoint blocks during DB writes.
- **Bulk create batch size (20)**: Balances write latency vs throughput. At 10 chunks/second, flushes every 2 seconds. Tune up for higher throughput.
- **Polling interval (500ms)**: The stream endpoint polls every 500ms for new rows. This adds up to 500ms latency for live-tailing. Acceptable for v1. Could reduce to 100ms if needed.
- **Worker thread per stream**: Each `GET /sessions/{id}/stream` connection holds a WSGI worker thread. With gunicorn default 2-4 workers, concurrent streams are limited. For production: use gevent workers or migrate to ASGI.
- **Storage**: ~240KB per 10-minute session. SQLite handles tens of GB. No immediate concern.

## References

- Research: `thoughts/research/2026-04-16-session-based-execution.md`
- Prior research: `thoughts/research/2026-04-16-sprites-deep-dive.md`
- Original API plan: `thoughts/plans/2026-04-15-fairy-api.md`
