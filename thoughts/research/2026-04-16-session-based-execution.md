---
date: 2026-04-16T04:36:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 7ca7e0ed13ec6deab60e99ee16f5368e95798719
branch: main
repository: ravi-hq/fairy
topic: "Session-based execution with stored logs — decoupling create from stream"
tags: [research, team-research, sessions, logs, streaming, architecture]
status: complete
method: agent-team
team_size: 3
tracks: [sprites-sessions, db-log-patterns, django-streaming]
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: Session-Based Execution with Stored Logs

**Date**: 2026-04-16
**Researcher**: Claude Code (team-research)
**Git Commit**: [`7ca7e0e`](https://github.com/ravi-hq/fairy/commit/7ca7e0ed13ec6deab60e99ee16f5368e95798719)
**Branch**: `main`
**Repository**: ravi-hq/fairy
**Method**: Agent team (3 specialist researchers)

## Research Question

How to restructure the fairy API so that `POST /run` records the session and returns immediately (with an ID), and a separate endpoint streams the logs — ideally storing all logs in the database associated with the session.

## Summary

**Sprites cannot store or replay output server-side.** The SDK has no scrollback replay, no output retrieval after command exit, and `max_run_after_disconnect` is not exposed in sprites-py. This means fairy must capture and store logs itself. The recommended architecture: modify the existing `QueueWriter` to dual-write (SSE stream + database), add `AgentSession` and `AgentSessionLog` models, and create a new streaming endpoint that tails the log table. Django's existing `StreamingHttpResponse` under WSGI handles this with no new dependencies.

## Research Tracks

### Track 1: Sprites Session Persistence
**Researcher**: sprites-researcher
**Scope**: sprites-py v0.0.1-rc37 SDK source code

#### Findings:
1. **attach_session exists but does NOT replay output** — `sprite.attach_session(session_id)` connects to `/v1/sprites/{name}/exec/{session_id}` and streams live output from the attach point forward. No scrollback/history replay. (`sprite.py:345`, `websocket.py:67`)
2. **max_run_after_disconnect not in SDK** — The term doesn't exist in sprites-py. `detachable: bool` is defined in `SpawnOptions` (`types.py:41`) but never wired into `Cmd.__init__` (`exec.py:36-76`). Python SDK cannot create sessions that survive client disconnect.
3. **list_sessions returns metadata only** — `Session` has `id`, `command`, `workdir`, `created`, `is_active`, `bytes_per_second`, `tty`, `last_activity`. No output data, no exit code. (`types.py:95-104`)
4. **No way to retrieve output from completed sessions** — Once the WebSocket closes, `WSCommand._stdout_buffer/_stderr_buffer` are gone. No `/exec/{id}/output` endpoint. (`websocket.py:51-52`)
5. **Filesystem redirect is a viable fallback** — Wrapper script can redirect to `/output.log`, then read via `sprite.filesystem()` after command exits. Downside: no live streaming.
6. **Sprites persist if not deleted** — `delete_sprite()` is the only deletion path. Skipping it keeps the Sprite alive (with cost), filesystem intact. Useful for async read-after-complete patterns.

### Track 2: Database Log Storage Patterns
**Researcher**: db-log-researcher
**Scope**: Django ORM patterns, existing fairy models

#### Findings:
1. **Individual rows beat blob or JSONField** — Single TextField append causes write amplification and row locking. JSONField has the same problem. Separate rows: append-only INSERTs, natural cursor via auto-increment PK, `bulk_create` works perfectly.
2. **Session model design** — `AgentSession`: UUID PK, user FK, runtime, sprite_name, status (pending/running/completed/failed), exit_code, created_at, updated_at.
3. **Write pattern: buffered bulk_create** — Buffer 10-20 chunks, flush with `bulk_create`. One-by-one `create()` adds ~1-5ms per event on SQLite. Batching reduces to ~1 round-trip per 10 events.
4. **Tailing query uses PK as cursor** — `AgentSessionLog.objects.filter(session_id=sid, id__gt=after_id).order_by("id")[:100]`. Composite index `(session_id, id)` makes this O(log n).
5. **Running → complete transition** — Two-condition check: `status in ("completed", "failed")` AND log query returns empty. Prevents race where client sees completion before final log flush.
6. **Storage estimate** — ~1,200 chunks per 10-minute session, ~240KB. Worst case 2-5MB. SQLite handles tens of GB comfortably.

### Track 3: Django Async Streaming
**Researcher**: django-streaming-researcher
**Scope**: Django 5.1 async support, current fairy server config

#### Findings:
1. **Django 5.1 supports StreamingHttpResponse with sync generators** — Async generators require ASGI, but sync generators work under WSGI (current setup).
2. **WSGI only, no ASGI** — `src/config/wsgi.py` exists, no `asgi.py`. Dev server runs via `manage.py runserver`.
3. **SSE works without django-channels** — Plain `StreamingHttpResponse` with `content_type="text/event-stream"` is sufficient. Already used in `views.py:86-89`.
4. **"Stream from DB then poll" pattern** — Replay all existing rows, poll for new rows with `time.sleep(0.5)`, terminate when session status is complete and no new rows.
5. **Connection keep-alive** — Send heartbeat comments every 15-30s. Gunicorn `--timeout` must exceed max stream duration. `X-Accel-Buffering: no` already set.
6. **ASGI migration is minimal but optional** — Add `asgi.py`, add `uvicorn` dependency, swap to `ASGI_APPLICATION`. Not needed for v1.

## Cross-Track Discoveries

- **Sprites can't store output → fairy must dual-write**: The `QueueWriter` in `stream.py:13-28` is already the interception point for all output chunks. Adding a DB write here captures logs without changing the Sprites integration at all.
- **SQLite WAL mode is a prerequisite**: The current default DELETE journal mode blocks readers during writes. The streaming endpoint would stall while the background thread writes log chunks. Fix: `PRAGMA journal_mode=WAL` in database settings.
- **TaggingQueueWriter needed**: Current `QueueWriter` is used for both stdout and stderr (`stream.py:48-49`) with no distinction. The `AgentSessionLog.stream` field needs to know which stream each chunk came from. Minimal change: wrap stream name alongside bytes in the queue.

## Proposed Architecture

### New Models

```python
class AgentSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    runtime = models.CharField(max_length=32)
    prompt = models.TextField()
    sprite_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, default="pending")
    # pending → running → completed / failed
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_sessions"

class AgentSessionLog(models.Model):
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name="logs")
    stream = models.CharField(max_length=6)  # "stdout" or "stderr"
    data = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_session_logs"
        indexes = [models.Index(fields=["session", "id"])]
```

### New Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST /run` | Create session, start execution in background thread, return session info | Returns `{id, status, stream_url}` |
| `GET /sessions/{id}` | Get session metadata | Returns `{id, runtime, status, exit_code, created_at}` |
| `GET /sessions/{id}/stream` | Stream logs via SSE | Replays stored logs, then tails for new ones |

### Execution Flow

```
POST /run
  1. Validate request (runtime, prompt, api_key)
  2. Create AgentSession (status="pending")
  3. Start background thread:
     a. Create Sprite
     b. Write wrapper script
     c. Execute command with dual-write QueueWriter
        - Each chunk → DB (AgentSessionLog) + internal queue
     d. On exit: update AgentSession.status, delete Sprite
  4. Return 202 {id: <uuid>, stream_url: "/sessions/<uuid>/stream"}

GET /sessions/{id}/stream
  1. Fetch all existing AgentSessionLog rows (id > 0), yield as SSE
  2. Poll for new rows every 500ms
  3. Send heartbeat comment every 15s
  4. When session.status in (completed, failed) AND no new rows → yield exit event, close
```

### Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Log storage | Individual DB rows | Append-only, natural cursor, no write amplification |
| Write pattern | Buffered bulk_create (batch=10-20) | Balances latency vs round-trips on SQLite |
| Background execution | Thread (not Celery) | v1 simplicity, already using threads for streaming |
| Stream protocol | SSE (text/event-stream) | Already proven in current codebase |
| Server mode | WSGI (no change) | Works today, ASGI optional later |
| SQLite journal | WAL mode (new) | Required for concurrent read+write |

### Changes to Existing Code

| File | Change |
|------|--------|
| `models.py` | Add `AgentSession`, `AgentSessionLog` |
| `stream.py` | Add `TaggingQueueWriter` (wraps stream name with bytes), add DB write in streaming loop |
| `views.py` | Refactor `run_agent` to return session ID instead of streaming. Add `get_session` and `stream_session` views |
| `urls.py` | Add `/sessions/<uuid:id>` and `/sessions/<uuid:id>/stream` routes |
| `settings.py` | Add SQLite WAL mode pragma |
| `admin.py` | Register `AgentSession`, `AgentSessionLog` |

## Code References

| File | Tracks | Findings | Link |
|------|--------|----------|------|
| `src/fairy/stream.py:13-28` | 1, 2 | QueueWriter is the dual-write interception point | — |
| `src/fairy/stream.py:48-49` | 2 | stdout/stderr both use QueueWriter, no distinction | — |
| `src/fairy/views.py:58-89` | 1, 3 | Current create+stream flow to refactor | — |
| `src/fairy/models.py` | 2 | Existing models, new session/log models go here | — |
| `src/config/settings.py:54-59` | 2, 3 | Database config, needs WAL pragma | — |
| `.venv/.../sprites/sprite.py:345` | 1 | attach_session implementation | — |
| `.venv/.../sprites/types.py:95-104` | 1 | Session dataclass (no output field) | — |

## Historical Context

- `thoughts/research/2026-04-15-sprites-platform-research.md` — Full platform overview, confirms no server-side output storage
- `thoughts/research/2026-04-16-sprites-deep-dive.md` — SDK surface catalog, documents attach_session and list_sessions
- `thoughts/plans/2026-04-15-fairy-api.md` — Original API plan (single create+stream endpoint)

## Open Questions

1. **Detachable sessions via raw WebSocket** — The SDK doesn't expose `detachable`, but the Sprites server may support it as a query param. If so, the background thread could disconnect and the Sprite would keep running. Worth testing with a raw WebSocket call.
2. **Log retention policy** — How long to keep session logs? Time-based cleanup job needed for production.
3. **Authentication** — `POST /run` and `GET /sessions/{id}/stream` need auth. The `APIKey` model exists but isn't wired to views yet.
4. **Multiple concurrent streams** — Can multiple clients stream the same session? The DB-tailing pattern supports this naturally (each client maintains its own cursor).
5. **Large output handling** — If an agent produces very large file outputs (>5MB), should we truncate or paginate the log table?
