# Streaming High-Volume Readiness Implementation Plan

## Overview

Fix the streaming stack's scalability gaps identified in
`thoughts/research/2026-04-19-streaming-high-volume-readiness.md`. Three PRs
land in sequence: (1) config + capacity, (2) producer + consumer
robustness, (3) operational hygiene (retention + watchdog).

The critical finding: under today's config the web service saturates at
**3 concurrent SSE clients** and `AgentSessionLog` has no retention — the
`basic-256mb` Postgres plan fills in ~2 days at 100 sessions/day. Every
other issue (producer stall, stuck sessions, slow-client DoS, missing
reconnect cursor) compounds these two.

## Research Summary

Research conducted by two agent teams:
- **Risk research** (`thoughts/research/2026-04-19-streaming-high-volume-readiness.md`):
  5 specialist tracks surveyed the SSE consumer, log producer, DB shape,
  client UX, and prior `thoughts/` context.
- **Pattern research** (this plan): 5 specialist tracks surveyed config &
  deploy conventions, producer robustness patterns, scheduling/retention
  precedents, API-evolution patterns, and test harness conventions — so
  every fix follows existing style.

### Key discoveries (with refs):
- **Gunicorn 25.3.0 bundles `gthread`** — no extra dep (`pyproject.toml:28`).
- **Procrastinate 3.8.1 supports `@app.periodic(cron=...)`** — existing
  worker process runs periodic tasks. No new Render cron service required
  (`pyproject.toml:25`).
- **`AgentSession.updated_at`** (`auto_now=True`, `models/sessions.py:39`)
  is the right signal for the stuck-session watchdog; `AgentSessionLog`
  is not indexed on `created_at`.
- **`render.yaml` `plan:` field controls the Postgres plan** — change
  there + push to upgrade (`render.yaml:8`).
- **No staging environment exists** (`render.yaml:1-93`). Direct-to-prod.
  Each PR needs a rollback plan (revert commit + push).
- **`stream.py` has zero unit tests today** — new test file needed.
- **No `freezegun` or `tenacity` in deps** — use `.update(updated_at=...)`
  to backdate rows in tests; use inline retry loops (no new libs).
- **`conn_max_age=600` (`settings.py:72`) + gthread `--threads=8` × 3
  workers = 24 connections**, which exceeds `basic-256mb` Postgres's 25
  max_connections once worker connections are added. Upgrading Postgres
  plan is mandatory, not optional, for this rollout.

## Current State Analysis

- **Web**: 3 sync Gunicorn workers. Each SSE stream pins one worker for the
  session's lifetime (up to ~10 min). Structural cap: 3 concurrent streams.
- **Worker**: single Procrastinate process, default `--concurrency=1`.
  Turns serialize.
- **Producer**: `TaggingQueueWriter.put()` is unconditionally blocking; a
  slow DB blocks the SDK's stdout thread and can time out the agent. No
  `bulk_create` retry. `cmd_thread.join(timeout=5.0)` has no follow-up
  `is_alive()` check — can leak daemon threads silently.
- **Consumer**: `stream.py` polls every 500 ms. No write-timeout — a
  stalled client holds a worker indefinitely. No reconnect cursor —
  every reconnect replays from `id=0`.
- **DB**: Indexes are correct. `AgentSessionLog` grows unbounded; no
  retention logic exists anywhere (confirmed by exhaustive grep).
- **API docs**: `turn_start` event emitted but absent from
  `site/docs/api/streaming.md` and `docs/openapi.yaml`.

## Desired End State

- **Web**: `gthread` workers with `--threads=8` → up to 24 concurrent SSE
  streams. Slow clients can no longer pin workers indefinitely.
- **Worker**: `--concurrency=4` so up to 4 turns can run in parallel.
- **Producer**: `bulk_create` retries transient DB failures (3 attempts,
  exponential backoff); queue uses bounded `put(timeout=5.0)` with
  drop-and-count; leaked daemon threads are detected and reported.
- **Consumer**: stream closes cleanly if the client stops reading within
  N seconds; reconnects resume from `Last-Event-ID` (or `?since=<id>`).
- **DB**: `AgentSessionLog` rows (and their parent sessions) older than
  30 days are purged nightly. `basic-256mb` → `standard-1gb` Postgres
  plan (97 max_connections).
- **Ops**: sessions stuck in `running` > 15 min are automatically marked
  `failed` by a watchdog, surfacing a terminal event to the SSE stream.
- **Docs**: `turn_start` event documented; resume cursor documented.

### Verification:
- After PR 1: `curl` 10 parallel SSE streams simultaneously against a
  long-running session → all succeed; `/health` still responds.
- After PR 2: kill a client mid-stream → worker returns within 30 s;
  reconnect with `Last-Event-ID` → no replay duplication.
- After PR 3: create a session with `updated_at` backdated 20 min + status
  `running` → watchdog flips to `failed` within 5 min. Completed session
  with logs dated 31 days ago → deleted by nightly job.

## What We're NOT Doing

- **Not switching to ASGI** (explicitly rejected in prior research).
- **Not switching to Redis/Celery/Dramatiq** — Postgres-backed Procrastinate
  is already in place and adequate.
- **Not changing the DB-tailing SSE pattern** — it's the right fit.
- **Not making `failed` sessions resumable** (PR #48 decision stands).
- **Not eliminating the inner daemon thread in `_execute_turn_body`** —
  explicitly deferred as a separate optimization per procrastinate-worker plan.
- **Not adding `AgentSession.completed_at`** — `updated_at` is sufficient
  for watchdog.
- **Not validating `Last-Event-ID` against a session-row-existence check** —
  lenient behavior (silent resume past deleted rows) preserves UX after
  retention.
- **Not building an admin audit log of purges** — the periodic task itself
  logs counts; that's enough for v1.

## Implementation Approach

Three PRs, shipped in order. PR 1 is atomic infrastructure (no code
changes). PRs 2 and 3 can ship in parallel after PR 1 lands since they
touch disjoint file sets.

Every PR includes: automated verification (ruff + pytest), manual
verification against the deployed service, and an explicit rollback
procedure (revert + push).

## File Ownership Map

| File | PR | Change type |
|------|----|-------------|
| `render.yaml` | 1 | modify (web startCommand, worker startCommand, DB plan) |
| `src/agent_on_demand/session_service/tasks.py` | 2 | modify (retry, backpressure, thread cleanup) |
| `src/agent_on_demand/stream.py` | 2 | modify (write timeout, emit `id:`, honor cursor) |
| `src/agent_on_demand/views/sessions.py` | 2 | modify (parse `Last-Event-ID` / `?since`) |
| `site/docs/api/streaming.md` | 2 | modify (turn_start, resume, `id:` field) |
| `docs/openapi.yaml` | 2 | modify (`since` param, event list) |
| `.claude/skills/agent-on-demand-api.md` | 2 | modify (turn_start event) |
| `tests/test_stream.py` | 2 | create |
| `tests/test_tasks.py` | 2 | modify (retry/backpressure/cleanup tests) |
| `tests/e2e/test_sessions.py` | 2 | modify (`Last-Event-ID` e2e, `@slow`) |
| `src/agent_on_demand/session_service/maintenance.py` | 3 | create |
| `src/agent_on_demand/migrations/0013_add_maintenance_indexes.py` | 3 | create |
| `src/agent_on_demand/admin.py` | 3 | modify (bulk-purge action) |
| `tests/test_maintenance.py` | 3 | create |

No file appears in more than one PR.

---

## PR 1: Config & capacity

### Overview
Atomic infra bump. No code changes. Switches the web service to gthread
workers, sets Procrastinate worker concurrency, and upgrades Postgres.

These ship together because gthread `--threads=8` × 3 workers + Procrastinate
`--concurrency=4` will exceed `basic-256mb`'s 25 max_connections. Upgrading
Postgres first (or in the same push) is required.

### Changes Required

#### 1. `render.yaml` — web startCommand

**File**: `render.yaml:18-25`

Replace startCommand with:

```yaml
startCommand: >-
  uv run gunicorn config.wsgi:application
  --bind 0.0.0.0:$PORT
  --workers 3
  --worker-class gthread
  --threads 8
  --graceful-timeout 650
  --timeout 700
  --max-requests 1000
  --max-requests-jitter 50
```

Changes: add `--worker-class gthread --threads 8`. Everything else
unchanged. `--timeout 700` stays (arbiter→worker heartbeat, not
per-request).

#### 2. `render.yaml` — worker startCommand

**File**: `render.yaml:56-57`

Replace with:

```yaml
startCommand: >-
  uv run python manage.py procrastinate worker
  --concurrency 4
```

Verify during rollout: Procrastinate's `--concurrency` caps in-flight
coroutines. Our tasks are sync and dispatch via `run_in_executor`. Python's
default executor is large enough (`min(32, cpu_count+4)`) that the
`--concurrency=4` semaphore is the effective cap. If under load we observe
>4 concurrent turns (via posthog), add explicit executor config in a
follow-up.

#### 3. `render.yaml` — Postgres plan

**File**: `render.yaml:7`

Change:

```yaml
plan: basic-256mb
```

to:

```yaml
plan: standard-1gb
```

standard-1gb: 1 GB RAM, ~97 max_connections, ~$20/mo.

### Success Criteria

#### Automated:
- [ ] `render config validate` passes (if available locally)

#### Manual:
- [ ] After deploy, `GET /health` still 200 under 10 parallel `curl` SSE
      streams against a completed session.
- [ ] `SELECT count(*) FROM pg_stat_activity WHERE datname='agent_on_demand'`
      shows ~4–12 connections at rest, not at the 25 ceiling.
- [ ] Procrastinate worker log shows up to 4 concurrent task executions under load.
- [ ] Honeycomb/Posthog: confirm `session.execute_turn` spans can overlap.

### Rollback

Revert the commit and push. Render redeploys the previous `render.yaml`.
Postgres plan downgrade will require DB plan review in the Render GUI.

### Gate

Hold 24 hours of production observation before merging PR 2 or PR 3.

---

## PR 2: Producer & consumer robustness

### Overview
Make the hot path survive transient DB failures, bounded queues, leaked
threads, slow clients, and reconnects — while keeping API contracts
additive only.

### Parallel tracks within PR 2
All changes ship in one PR since they touch overlapping files
(`tasks.py`, `stream.py`, `views/sessions.py`, docs). Splitting further
would cause merge churn.

### Changes Required

#### 1. `bulk_create` retry with backoff

**File**: `src/agent_on_demand/session_service/tasks.py` (modify around
line 329-332, inside `_execute_turn_body`)

Replace:

```python
def _flush_buffer():
    if db_buffer:
        AgentSessionLog.objects.bulk_create(db_buffer)
        db_buffer.clear()
```

with:

```python
_BULK_CREATE_DELAYS = (0.1, 0.3, 1.0)

def _flush_buffer():
    if not db_buffer:
        return
    for attempt, delay in enumerate(_BULK_CREATE_DELAYS, 1):
        try:
            AgentSessionLog.objects.bulk_create(db_buffer)
            db_buffer.clear()
            return
        except Exception as e:
            if attempt == len(_BULK_CREATE_DELAYS):
                logger.exception("bulk_create exhausted retries")
                with posthog.new_context():
                    posthog.identify_context(str(session.user_id))
                    posthog.capture(
                        "session.log_write_retry_exhausted",
                        properties={
                            "session_id": str(session.id),
                            "turn_number": turn.turn_number,
                            "runtime": session.runtime,
                            "dropped_chunks": len(db_buffer),
                        },
                    )
                raise
            close_old_connections()
            time.sleep(delay)
```

Emits a span inside the retry loop (via `tracer.start_as_current_span`) is
*not* added here — existing span around the whole turn body already covers
the DB write subspan implicitly, and adding nested spans adds noise.

#### 2. Bounded `queue.put` with drop-and-count

**File**: `src/agent_on_demand/session_service/tasks.py:70-87`

Modify `TaggingQueueWriter`:

```python
class TaggingQueueWriter(io.RawIOBase):
    def __init__(self, q: queue.Queue, stream: str):
        self._queue = q
        self._stream = stream
        self.drop_count = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        try:
            self._queue.put(TaggedChunk(self._stream, data), timeout=5.0)
        except queue.Full:
            self.drop_count += 1
            if self.drop_count == 1:
                logger.warning(
                    "TaggingQueueWriter: output queue full, dropping chunks"
                )
        return len(data)
```

After the consumer loop in `_execute_turn_body`, if either writer
`drop_count > 0`, emit a posthog event:

```python
total_drops = stdout_writer.drop_count + stderr_writer.drop_count
if total_drops > 0:
    with posthog.new_context():
        posthog.identify_context(str(session.user_id))
        posthog.capture(
            "session.output_chunks_dropped",
            properties={
                "session_id": str(session.id),
                "turn_number": turn.turn_number,
                "runtime": session.runtime,
                "dropped_count": total_drops,
            },
        )
```

Requires plumbing `stdout_writer` and `stderr_writer` as local
variables (they're currently inline in `_run_command`).

#### 3. `cmd_thread.is_alive()` check after join

**File**: `src/agent_on_demand/session_service/tasks.py:388`

Replace:

```python
cmd_thread.join(timeout=5.0)
```

with:

```python
cmd_thread.join(timeout=5.0)
if cmd_thread.is_alive():
    logger.error(
        "session %s turn %s: command thread still alive after join",
        session.id,
        turn.turn_number,
    )
    with posthog.new_context():
        posthog.identify_context(str(session.user_id))
        posthog.capture(
            "session.cmd_thread_leaked",
            properties={
                "session_id": str(session.id),
                "turn_number": turn.turn_number,
                "runtime": session.runtime,
            },
        )
```

No forceful termination — Python can't kill a thread. The leak is
reported; the turn proceeds to mark itself `failed` or `completed` per
the existing `result_holder` state.

#### 4. SSE write timeout / idle client detection

**File**: `src/agent_on_demand/stream.py` (modify around lines 20-57)

Add an **idle timeout**: if no new chunks arrive and the session is still
`running`, keep polling up to `STREAM_IDLE_LIMIT=600` seconds. After
that, break and emit a terminal "stale" event. This bounds the maximum
time a slow client can hold a worker.

Note: detecting actual client disconnect in a Gunicorn sync/gthread view
is *not* reliably possible in Django — the generator only knows when it
writes and the socket errors. Gunicorn's internal write machinery will
raise an exception on a dead socket, which will propagate out of the
`yield` and unwind `event_generator`. We don't need to catch it; we need
to ensure we don't stay in `time.sleep` forever.

Replace the body of `stream_session_from_db`:

```python
STREAM_IDLE_LIMIT = 600  # seconds of no new chunks before giving up

def stream_session_from_db(session_id: str) -> Generator[str, None, None]:
    last_id = 0
    last_turn_id = None
    last_heartbeat = time.time()
    last_chunk_time = time.time()

    while True:
        chunks = list(
            AgentSessionLog.objects.filter(session_id=session_id, id__gt=last_id)
            .order_by("id")
            .values("id", "stream", "data", "turn_id", "turn__turn_number")[:100]
        )

        if chunks:
            last_chunk_time = time.time()

        for chunk in chunks:
            last_id = chunk["id"]
            turn_id = chunk["turn_id"]
            if turn_id is not None and turn_id != last_turn_id:
                yield _format("turn_start", chunk["id"], {"turn": chunk["turn__turn_number"]})
                last_turn_id = turn_id
            yield _format("output", chunk["id"], {
                "stream": chunk["stream"],
                "data": chunk["data"],
                "turn": chunk["turn__turn_number"],
            })

        session = AgentSession.objects.get(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield _format("terminated", last_id, {"message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield _format("error", last_id, {"message": "Session failed"})
            else:
                yield _format("exit", last_id, {"code": session.exit_code})
            break

        if time.time() - last_chunk_time > STREAM_IDLE_LIMIT:
            yield _format("stale", last_id, {
                "message": f"No output for {STREAM_IDLE_LIMIT}s"
            })
            break

        now = time.time()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        time.sleep(0.5)


def _format(event_type: str, log_id: int, payload: dict) -> str:
    return json.dumps({"type": event_type, "id": log_id, **payload})
```

Note: we embed the log ID inside the JSON payload as `"id": <int>` in
addition to the SSE `id:` field (emitted by the view wrapper). This makes
parsing robust whether the client uses `EventSource` (reads SSE `id:`) or
parses `data:` JSON lines.

The signature of the generator also needs a new `since` parameter —
see change #5.

#### 5. `Last-Event-ID` + `?since=<id>` parsing

**File**: `src/agent_on_demand/views/sessions.py:298-320`

Modify `stream_session`:

```python
@require_GET
@require_api_key
def stream_session(request, session_id):
    try:
        session = AgentSession.objects.get(pk=session_id, user=request.user)
    except (AgentSession.DoesNotExist, ValueError):
        return JsonResponse({"detail": "Session not found"}, status=404)

    raw = request.META.get("HTTP_LAST_EVENT_ID") or request.GET.get("since", "0")
    try:
        since = max(0, int(raw))
    except ValueError:
        return JsonResponse({"detail": "since must be an integer"}, status=400)

    def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'runtime': session.runtime, 'session_id': str(session.id)})}\n\n"

        for event in stream_session_from_db(str(session.id), since=since):
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

Validation is lenient: any int ≥ 0 is accepted. If the referenced log
row was deleted by retention (PR 3), the query silently starts from the
next surviving row (already-correct behavior because the query is
`id__gt=since`, not an equality match).

And update `stream_session_from_db` signature:

```python
def stream_session_from_db(
    session_id: str, since: int = 0
) -> Generator[str, None, None]:
    last_id = since
    ...
```

#### 6. Documentation updates

**File**: `site/docs/api/streaming.md`

- Add `turn_start` row to the event-types table.
- Add "Resuming a stream" section covering `Last-Event-ID` header and
  `?since=<id>` query param, with a note that stale cursors silently
  resume from the next surviving event.
- Update the example stream to show `id:` lines.
- Update the Python client example to track and pass `last_event_id`.

**File**: `docs/openapi.yaml:347-360`

Add `since` query parameter and update the description to list
`turn_start` in the event enumeration.

**File**: `.claude/skills/agent-on-demand-api.md:177-188`

Add `turn_start` to the event list.

#### 7. Tests

**New file**: `tests/test_stream.py`

Follows patterns from `test_api.py:329` (consume via
`b"".join(resp.streaming_content).decode()`).

- `test_stream_replays_from_since_cursor`: seed 10 log rows, call with
  `since=5`, assert only 5 events emitted and first `id:` is 6.
- `test_stream_rejects_non_integer_since`: 400.
- `test_stream_lenient_on_stale_cursor`: seed logs 100-200, call with
  `since=50` (below range), assert all 100 events appear (no 400, no
  replay-from-zero).
- `test_stream_emits_id_field`: assert wire format includes `id: N\n`
  before `data:` for output events.
- `test_stream_emits_turn_start`: assert `turn_start` event emitted on
  turn boundary with correct `id:`.
- `test_stream_idle_timeout`: patch `time.time` + `time.sleep`, simulate
  a `running` session with no new chunks → `stale` terminal event after
  10 min.

**File**: `tests/test_tasks.py` (add cases)

- `test_bulk_create_retries_on_transient_failure`: mock `bulk_create`
  to raise twice then succeed; assert 3 calls, all chunks written, no
  posthog exhaustion event.
- `test_bulk_create_exhausts_retries_and_raises`: mock `bulk_create` to
  always raise; assert 3 calls, turn marks `failed`, posthog
  `session.log_write_retry_exhausted` emitted.
- `test_queue_full_drops_chunk_and_counts`: monkeypatch `output_q` to
  `Queue(maxsize=1)`, force put-timeout, assert `drop_count > 0` and
  posthog event emitted.
- `test_cmd_thread_leak_detected`: stub `cmd_thread.is_alive` to return
  True; assert posthog `session.cmd_thread_leaked` emitted.

**File**: `tests/e2e/test_sessions.py` (add case, mark `@slow`)

- `test_sse_reconnect_via_last_event_id`: start session, consume stream
  partway, capture last `id:`, reconnect with `Last-Event-ID` header,
  assert no duplicate events.

### Success Criteria

#### Automated:
- [ ] `make lint` passes.
- [ ] `make test` passes (unit + integration).
- [ ] `make test-e2e-fast` passes.
- [ ] `make test-e2e` passes (includes `@slow` reconnect test).

#### Manual:
- [ ] `curl -N $BASE/sessions/$SID/stream` against an active session,
      then Ctrl+C — worker returns within 30 s (verify via Render logs /
      honeycomb span end-time).
- [ ] `curl -N "$BASE/sessions/$SID/stream?since=50"` resumes mid-stream,
      no events with `id ≤ 50` appear.
- [ ] Provoke a queue-full scenario by slowing DB (e.g., `pg_sleep` in a
      long transaction on the web connection path) and driving a noisy
      agent → posthog `session.output_chunks_dropped` event appears,
      agent does not hang beyond 5 s per write.

### Rollback

Revert commit + push. Clients using `Last-Event-ID` will see full replay
on their next reconnect (no data loss). No DB state changes.

---

## PR 3: Operational hygiene (retention + watchdog)

### Overview
Two new periodic tasks registered on the existing Procrastinate worker
plus a migration to make the watchdog query fast.

### Dependencies
Requires PR 1 deployed (so the worker process is running and can pick up
periodic task registrations).

### Changes Required

#### 1. New module: `session_service/maintenance.py`

**File**: `src/agent_on_demand/session_service/maintenance.py` (new)

```python
"""Periodic maintenance tasks.

Two periodic tasks run on the existing Procrastinate worker:

- `purge_old_session_logs` (daily at 02:00 UTC): deletes AgentSession
  rows in terminal states older than 30 days, which cascades to their
  AgentSessionLog rows.
- `mark_stuck_sessions_failed` (every 5 minutes): flips sessions stuck
  in `running` state for >15 minutes to `failed`. The watchdog uses
  AgentSession.updated_at, not AgentSessionLog.created_at, because the
  former is auto_now-updated on every state transition and is
  index-friendly (see migration 0013).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app

from agent_on_demand.models import AgentSession

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
WATCHDOG_IDLE_MINUTES = 15
PURGE_BATCH_SIZE = 500
TERMINAL_STATUSES = ("completed", "failed", "terminated")


@procrastinate_app.task(
    queue="maintenance", name="purge_old_session_logs", pass_context=False
)
@procrastinate_app.periodic(cron="0 2 * * *", periodic_id="purge_old_session_logs")
def purge_old_session_logs(timestamp: int) -> None:
    close_old_connections()
    try:
        cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
        total = 0
        while True:
            ids = list(
                AgentSession.objects.filter(
                    status__in=TERMINAL_STATUSES,
                    updated_at__lt=cutoff,
                ).values_list("id", flat=True)[:PURGE_BATCH_SIZE]
            )
            if not ids:
                break
            deleted, _ = AgentSession.objects.filter(id__in=ids).delete()
            total += deleted
        logger.info("purge_old_session_logs: deleted %d sessions", total)
    finally:
        close_old_connections()


@procrastinate_app.task(
    queue="maintenance", name="mark_stuck_sessions_failed", pass_context=False
)
@procrastinate_app.periodic(cron="*/5 * * * *", periodic_id="mark_stuck_sessions_failed")
def mark_stuck_sessions_failed(timestamp: int) -> None:
    close_old_connections()
    try:
        cutoff = timezone.now() - timedelta(minutes=WATCHDOG_IDLE_MINUTES)
        updated = AgentSession.objects.filter(
            status="running", updated_at__lt=cutoff
        ).update(status="failed", updated_at=timezone.now())
        if updated:
            logger.warning(
                "mark_stuck_sessions_failed: flipped %d sessions to failed",
                updated,
            )
    finally:
        close_old_connections()
```

The `timestamp: int` parameter is Procrastinate's periodic contract —
keep it as a positional argument even though we don't use it.

#### 2. Register maintenance module

**File**: `src/agent_on_demand/apps.py` (modify)

Ensure the module is imported at app-ready time so Procrastinate's
periodic scheduler sees the decorators. The existing `tasks.py` is
already imported through Django's app loading; verify and, if
`maintenance.py` isn't auto-imported, add:

```python
from django.apps import AppConfig


class AgentOnDemandConfig(AppConfig):
    name = "agent_on_demand"
    label = "fairy"

    def ready(self) -> None:
        from agent_on_demand.session_service import (  # noqa: F401
            maintenance,
            tasks,
        )
```

#### 3. Migration 0013 — `(status, updated_at)` index

**File**: `src/agent_on_demand/migrations/0013_add_maintenance_indexes.py` (new)

```python
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("fairy", "0012_add_runtime_session_id")]

    operations = [
        migrations.AddIndex(
            model_name="agentsession",
            index=models.Index(
                fields=["status", "updated_at"],
                name="agentsession_status_upd_idx",
            ),
        ),
    ]
```

Index name is under Postgres's 63-char limit. No concurrent-index
option needed — `AgentSession` is small relative to logs.

#### 4. Admin bulk-purge action

**File**: `src/agent_on_demand/admin.py` (modify)

Add an admin action on `AgentSessionAdmin` following the existing
`terminate_sessions` pattern (`admin.py:218-264`):

```python
@admin.action(description="Purge selected sessions and their logs")
def purge_sessions(modeladmin, request, queryset):
    non_terminal = queryset.exclude(status__in=TERMINAL_STATUSES).count()
    if non_terminal:
        messages.warning(
            request,
            f"Skipped {non_terminal} non-terminal session(s); "
            "terminate them first.",
        )
    eligible = queryset.filter(status__in=TERMINAL_STATUSES)
    deleted, _ = eligible.delete()
    messages.success(request, f"Purged {deleted} session(s) and their logs.")
```

#### 5. Tests

**New file**: `tests/test_maintenance.py`

Follows the pattern in `test_tasks.py` — call task functions directly.
No `freezegun` — use `.update(updated_at=...)` to backdate rows.

- `test_purge_old_session_logs_deletes_old_terminal_sessions`
- `test_purge_old_session_logs_skips_recent_sessions`
- `test_purge_old_session_logs_skips_running_sessions`
- `test_purge_old_session_logs_cascades_to_logs`
- `test_purge_old_session_logs_batches_correctly`: seed > `PURGE_BATCH_SIZE` rows
- `test_mark_stuck_sessions_failed_flips_stuck_running`
- `test_mark_stuck_sessions_failed_leaves_recent_running_alone`
- `test_mark_stuck_sessions_failed_leaves_terminal_alone`

Plus a `close_old_connections` spy assertion on each task (matches
`test_tasks.py:187` pattern).

### Success Criteria

#### Automated:
- [ ] `make lint` passes.
- [ ] `make migrate` applies 0013 cleanly; `make test` passes.
- [ ] New tests in `test_maintenance.py` all pass.

#### Manual:
- [ ] On deploy, worker log shows periodic tasks registered at startup
      (Procrastinate logs this).
- [ ] After 5 min post-deploy, watchdog log line appears (either "flipped
      0 sessions" or "flipped N").
- [ ] Postgres `EXPLAIN` of the watchdog query uses the new index:
      ```sql
      EXPLAIN SELECT id FROM fairy_agentsession
      WHERE status='running' AND updated_at < NOW() - INTERVAL '15 min';
      ```
      → expects "Index Scan using agentsession_status_upd_idx".
- [ ] Seed a test session with backdated `updated_at` in staging (or a
      throwaway local DB) and confirm the watchdog flips it.

### Rollback

Revert commit + push. Migration 0013 is additive (adds an index); a
rollback can optionally leave the index in place (harmless) or run the
reverse migration if we want cleanliness. No destructive schema changes.

### Gate

Wait 48 hours after PR 3 deploys before considering the streaming-scale
initiative complete. Watch:
- Posthog `session.log_write_retry_exhausted`, `session.output_chunks_dropped`,
  `session.cmd_thread_leaked` — should be rare/zero.
- Worker log for first watchdog flip and first nightly purge.
- Honeycomb span counts for `session.execute_turn` overlapping up to 4.

---

## Testing Strategy

### Unit (per PR):
- PR 1: no new tests (config change only); verify nothing breaks.
- PR 2: new `tests/test_stream.py`; extensions to `tests/test_tasks.py`.
- PR 3: new `tests/test_maintenance.py`.

### Integration:
- All PRs run against the Django test client + SQLite (per
  `tests/conftest.py`). Thread-isolation behavior of `close_old_connections`
  is spied, not integration-tested (SQLite has different connection
  semantics than Postgres; the `test-researcher` note was explicit about this).

### E2E:
- PR 2: one new `@slow` e2e test for SSE reconnect via `Last-Event-ID`.
- PR 1 and PR 3 don't need e2e coverage — PR 1 is infra, PR 3's effects
  are observable only over time windows.

### Manual:
- After each PR, run the verification steps listed under "Success
  Criteria → Manual".

## Performance Considerations

- **PR 1** moves max concurrent SSE streams from 3 → 24. Connection pool
  grows from ~4 → up to ~29 persistent connections. `standard-1gb`
  Postgres (97 max_connections) provides headroom.
- **PR 2** adds at most 3 extra DB round-trips per turn on bulk_create
  retry (steady-state: zero; failure mode: 3). The 500 ms SSE poll loop
  is unchanged.
- **PR 3** adds two periodic tasks:
  - Retention: daily at 02:00 UTC; batched ORM delete with `PURGE_BATCH_SIZE=500`
    per batch. Even purging 10k sessions fits in ~20 batches.
  - Watchdog: every 5 min; single indexed UPDATE — cost is negligible.

## References

- Research: `thoughts/research/2026-04-19-streaming-high-volume-readiness.md`
- Prior streaming research: `thoughts/research/2026-04-19-threading-in-web-server.md`
- Prior threading decision: `thoughts/plans/2026-04-19-threading-architecture-decision.md`
- Procrastinate migration: `thoughts/plans/2026-04-19-procrastinate-worker.md`
- Admin bulk-action pattern: `src/agent_on_demand/admin.py:218-264`
- Existing task pattern: `src/agent_on_demand/session_service/tasks.py:104,264`
- Posthog event pattern: `src/agent_on_demand/session_service/tasks.py:252-261,420-432`
- Tracer pattern: `src/agent_on_demand/session_service/tasks.py:155-162,305-316`
- SSE test pattern: `tests/test_api.py:329,580`
