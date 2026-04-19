# Out-of-Process Worker via Procrastinate — Implementation Plan

## Overview

Move session execution off the web process. The web service creates the Session / Turn rows, enqueues a task into a Postgres-backed broker, and returns `202` to the client. A separate Render Background Worker service pulls the task, runs `sprite.command().run()`, streams log chunks into `AgentSessionLog`, and finalizes the session/turn state on completion.

The client contract is unchanged — `POST /sessions` still returns `202` with a stream URL; `GET /sessions/{id}/stream` still tails the DB. What changes is *where* the blocking SDK call happens.

Broker: **Procrastinate** using the existing Postgres. No Redis. Adds one Render service (~$7/mo).

Assumes the bug-fix PR [#48](https://github.com/ravi-hq/agent-on-demand/pull/48) is already merged.

## Research Summary

Backed by:
- [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md)
- [`thoughts/plans/2026-04-19-threading-architecture-decision.md`](/thoughts/plans/2026-04-19-threading-architecture-decision.md)

Key facts driving this plan:
- Today `run_turn` spawns a daemon `threading.Thread` per turn, which spawns a second daemon thread for the blocking `sprite.command().run()`. Both threads run inside the Gunicorn worker process.
- Gunicorn's graceful-timeout vs. 600 s turn limit mismatch is partially mitigated by PR #48 but ultimately bounded by whatever SIGKILL window Render allows. Moving execution to a separate service eliminates the mismatch entirely for the web process.
- SSE streaming reads `AgentSessionLog` rows from the DB. It doesn't care which process wrote them. This is the single most useful pre-existing property — migration inherits a working client contract for free.
- Procrastinate is the cleanest fit for Render + Postgres (no Redis), mature, supports sync tasks natively, and has first-class Django integration.

## Current State Analysis

- `POST /sessions` (`views/sessions.py::_create_session`) creates `AgentSession` + `SessionTurn` rows, calls `session_service.run_turn(...)`, returns `202`.
- `session_service.run_turn` spawns `threading.Thread(target=run_session_background, ...)`.
- `stream.run_session_background` (`src/agent_on_demand/stream.py`) does the per-turn DB state machine, spawns a second thread for `sprite.command().run()`, drains a queue of log chunks, bulk-inserts `AgentSessionLog`, and finalizes state.
- `POST /sessions/{id}/prompt` (`views/sessions.py::send_prompt`) does the same — creates a new turn row, calls `run_turn(..., mode="continue")`.
- `GET /sessions/{id}/stream` reads `AgentSessionLog` rows — unchanged in this plan.
- `render.yaml` has a single `web` service + one Postgres. No worker.

## Desired End State

1. `POST /sessions` and `POST /sessions/{id}/prompt` enqueue a Procrastinate task and return `202`. No `threading.Thread` spawned from our code anywhere in the web process.
2. A `worker` Render service runs `procrastinate worker`. It pulls tasks, runs the same per-turn work that `run_session_background` does today, writes to the DB.
3. The SSE endpoint works as-is — tails `AgentSessionLog`.
4. The inner daemon thread inside the task (the one wrapping `sprite.command().run()`) still exists — it's load-bearing for the producer-consumer pattern against the SDK's own event-loop thread, and eliminating it is a separate optimization. But our code spawns **zero threads in the web process**.
5. Worker deploys can drain gracefully via Procrastinate's shutdown handling (bounded by Render's SIGKILL window, which is unknown — see Open Questions).
6. Failed tasks retry automatically with configurable limits (Procrastinate built-in).

### Verification

- `make lint && make fmt` clean.
- `make test` passes. Tests that mocked `threading.Thread` in the web process now mock Procrastinate's in-memory connector instead; tests that exercised `run_session_background` directly invoke the task function directly.
- New unit test: `POST /sessions` enqueues exactly one task with the expected payload (no thread is spawned).
- New unit test: the task function, given fixtures for a session + turn + mocked sprite, performs the same DB state transitions today's `run_session_background` does.
- Manual smoke: `make dev` + run `procrastinate --app=... worker` in a second terminal. `curl` a session create and confirm the turn runs in the worker, not the dev server.
- `make test-e2e-fast` passes against a staging deploy with both web and worker services running.

## What We're NOT Doing

- **Not touching cancellation.** Terminating a session mid-run still just deletes the Sprite and marks the DB row `terminated`. The worker task eventually notices (via `SpriteError` from the WebSocket drop) and exits. Propagating a cancel signal to the worker mid-task is a future problem; the framework can do it but we're not wiring it up.
- **Not eliminating the inner daemon thread inside the task.** The one wrapping `sprite.command().run()` stays — it's the same producer-consumer pattern as today, just relocated. A follow-up can replace `cmd.run()` with direct `asyncio.run_coroutine_threadsafe` against the SDK's private `_run_async`.
- **Not tuning Render's SIGTERM→SIGKILL window.** We don't know what Render allows on any plan; this plan ships with whatever the default is and logs the outcome on the first deploy that coincides with an in-flight turn.
- **Not changing any API surface.** `POST /sessions` still returns `202`; `GET /sessions/{id}/stream` still SSE-tails. Clients see no difference except — eventually — that deploys no longer drop in-flight turns.
- **Not migrating other background work.** There is no other background work today. If we add more (cleanup jobs, scheduled tasks), those go through Procrastinate too, but that's future scope.
- **Not fixing Muddy Zone 8.** PR #48 already blunts the hazard by making `failed` terminal. The deeper fix (cancel-via-future) is orthogonal.

## Implementation Approach

One PR, four phases. Each phase is a self-contained commit; the PR is coherent end-to-end.

1. Add dependency + Django app + migrations + Render worker service entry. Everything required to have a runnable (but unused) worker in production.
2. Convert `run_session_background` into a Procrastinate task function. Task exists; web hasn't been flipped to use it yet. Threading code still lives alongside.
3. Flip `session_service.turn.run_turn` from `Thread.start()` to `task.defer()`. Delete threading machinery from `turn.py` and `stream.py`.
4. Update tests.

### Deploy sequencing (single deploy)

- Both services in `render.yaml` deploy together.
- `preDeployCommand` runs migrations on both (or the web service preDeploy runs all migrations, and the worker inherits the same DB schema).
- First in-flight request after deploy: web enqueues; worker dequeues. Works first time.
- If worker service fails to start, web still accepts requests but tasks pile up in the DB until worker recovers. This is the correct failure mode — requests aren't dropped, work is deferred. Flag it as a Risk below.

## File Ownership Map

| File | Change | Notes |
|------|--------|-------|
| `pyproject.toml` | modify | Add `procrastinate[django]` dependency. Pin to a current major. |
| `src/config/settings.py` | modify | Add `"procrastinate.contrib.django"` to `INSTALLED_APPS`. Minimal Procrastinate config pointing at existing `DATABASES["default"]`. |
| `src/agent_on_demand/session_service/tasks.py` | create | Declares the `execute_turn` Procrastinate task. Task body is the port of today's `_run_session_background_inner`. |
| `src/agent_on_demand/session_service/turn.py` | rewrite | `run_turn` becomes `execute_turn.defer(session_id=..., turn_id=..., user_id=..., runtime_name=..., prompt=..., mode=..., timeout=...)`. All threading goes away. |
| `src/agent_on_demand/stream.py` | modify | `run_session_background` + `_run_session_background_inner` move out (into `tasks.py`). `TaggingQueueWriter`, `TaggedChunk`, `_build_turn_command`, `stream_session_from_db` stay — they're shared with the task body and the SSE endpoint respectively. |
| `src/agent_on_demand/views/sessions.py` | no behavior change | Still calls `session_service.run_turn`. The function signature stays the same; only its internals change. |
| `render.yaml` | modify | Add a `worker` service entry. Same Docker-less runtime, same build command, `startCommand: uv run procrastinate --app=agent_on_demand.session_service.tasks.procrastinate_app worker`. |
| `tests/conftest.py` | modify | `fake_sprites` fixture gains a Procrastinate `InMemoryConnector` that captures deferred tasks. |
| `tests/test_api.py` | modify | Tests that capture `session_service.turn.threading.Thread` switch to capturing deferred tasks. Existing `test_run_session_background_persists_int_exit_code_on_exec_error` invokes the task function directly instead of the old module-level function. |
| `tests/test_tasks.py` (new) | create | Unit tests for the `execute_turn` task: state transitions, DB writes, failure paths. |
| `README.md` | modify | Note that running locally requires `procrastinate worker` in a second terminal. |
| `Makefile` | modify | Add `make worker` target: `uv run procrastinate --app=... worker`. |

## Phase 1: Dependency, migrations, Render service

### Changes Required

#### 1. `pyproject.toml`

Add to `dependencies`:

```toml
dependencies = [
    # ... existing ...
    "procrastinate[django]>=3.0",
]
```

Then:

```bash
uv sync
uv run python manage.py migrate procrastinate
```

#### 2. `src/config/settings.py`

Add Procrastinate's Django app:

```python
INSTALLED_APPS = [
    # ... existing apps ...
    "procrastinate.contrib.django",
    # fairy is already last
]
```

Procrastinate's Django contrib automatically uses `DATABASES["default"]` — no separate broker URL needed.

#### 3. `render.yaml`

Add a second service under `services`:

```yaml
services:
  - type: web
    # ... existing web service unchanged ...

  - type: worker
    name: agent-on-demand-worker
    runtime: python
    plan: starter
    buildCommand: pip install uv && uv sync
    startCommand: >-
      uv run procrastinate
      --app=agent_on_demand.session_service.tasks.procrastinate_app
      worker
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: agent-on-demand-db
          property: connectionString
      - key: DJANGO_SECRET_KEY
        fromService:
          name: agent-on-demand-api
          type: web
          envVarKey: DJANGO_SECRET_KEY
      - key: FIELD_ENCRYPTION_KEY
        fromService:
          name: agent-on-demand-api
          type: web
          envVarKey: FIELD_ENCRYPTION_KEY
      - key: DJANGO_DEBUG
        value: "false"
      - key: DJANGO_ALLOWED_HOSTS
        value: aod.ravi.id,agent-on-demand-api.onrender.com
      - key: PYTHON_VERSION
        value: "3.11.9"
```

Worker shares the DB, the Django secret key (for model signatures), and the field encryption key (for reading encrypted env vars on the Sprite). No health check (workers don't serve HTTP). No `preDeployCommand` — migrations run on the web service's preDeploy and are shared via Postgres.

### Verification

- Local: `uv sync && make migrate && uv run procrastinate --app=... worker` — worker starts without errors.
- Render staging deploy: worker service comes up green. Its logs show `Starting worker...`. No tasks are processed yet (none are being enqueued).

## Phase 2: Convert `run_session_background` into a task

### Changes Required

#### 1. `src/agent_on_demand/session_service/tasks.py` (new file)

```python
"""Procrastinate task: execute one turn of an agent session.

This runs in the worker process, not the web process. Its job is what today's
`stream.run_session_background` does — drive the per-turn DB state machine,
run the blocking SDK call, persist log chunks to `AgentSessionLog`, finalize.

The inner daemon thread that wraps `sprite.command().run()` stays here — it's
load-bearing for the producer-consumer pattern against the SDK's own
event-loop thread (which pushes log chunks into a queue via
`TaggingQueueWriter`). Eliminating it is a separate optimization.
"""

from __future__ import annotations

import io
import logging
import queue
import threading

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app
from sprites import ExecError

from agent_on_demand.models import AgentSession, AgentSessionLog, SessionTurn
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service import resume_session
from agent_on_demand.stream import (
    FLUSH_SIZE,
    TaggingQueueWriter,
    _SENTINEL,
    _build_turn_command,
)

logger = logging.getLogger(__name__)


@procrastinate_app.task(queue="sessions", name="execute_turn")
def execute_turn(
    *,
    session_id: str,
    turn_id: int,
    user_id: int,
    runtime_name: str,
    prompt: str,
    mode: str,
    timeout: float,
):
    """Run one turn. Arguments are JSON-serializable primitives; we re-fetch
    ORM rows and re-open the Sprite handle inside the task."""
    close_old_connections()
    try:
        _execute_turn_inner(
            session_id=session_id,
            turn_id=turn_id,
            user_id=user_id,
            runtime_name=runtime_name,
            prompt=prompt,
            mode=mode,
            timeout=timeout,
        )
    finally:
        close_old_connections()


def _execute_turn_inner(
    *,
    session_id: str,
    turn_id: int,
    user_id: int,
    runtime_name: str,
    prompt: str,
    mode: str,
    timeout: float,
):
    user = User.objects.get(pk=user_id)
    session = AgentSession.objects.get(pk=session_id)
    turn = SessionTurn.objects.get(pk=turn_id)
    runtime = RUNTIMES[runtime_name]
    sprite = resume_session(user, session.sprite_name)

    output_q: queue.Queue = queue.Queue(maxsize=4096)
    db_buffer: list[AgentSessionLog] = []
    result_holder: list = []

    def _flush_buffer():
        if db_buffer:
            AgentSessionLog.objects.bulk_create(db_buffer)
            db_buffer.clear()

    def _run_command():
        # NOTE: if you add DB writes inside this inner thread, wrap the body
        # in close_old_connections()/finally. It only reaches the SDK today.
        try:
            cmd = sprite.command(
                "bash",
                "-c",
                _build_turn_command(runtime, mode),
                cwd="/home/sprite",
                timeout=timeout,
            )
            cmd.stdin = io.BytesIO(prompt.encode("utf-8"))
            cmd.stdout = TaggingQueueWriter(output_q, "stdout")
            cmd.stderr = TaggingQueueWriter(output_q, "stderr")
            cmd.run()
            result_holder.append(("exit", 0))
        except ExecError as e:
            result_holder.append(("exit", e.exit_code()))
        except Exception as e:
            logger.exception(
                "session %s turn %s task raised", session.id, turn.turn_number
            )
            result_holder.append(("error", str(e)))
        finally:
            output_q.put(_SENTINEL)

    now = timezone.now()
    session.status = "running"
    session.save(update_fields=["status", "updated_at"])
    turn.status = "running"
    turn.started_at = now
    turn.save(update_fields=["status", "started_at"])

    cmd_thread = threading.Thread(target=_run_command, daemon=True)
    cmd_thread.start()

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
                turn=turn,
                stream=chunk.stream,
                data=chunk.data.decode("utf-8", errors="replace"),
            )
        )
        if len(db_buffer) >= FLUSH_SIZE:
            _flush_buffer()

    _flush_buffer()
    cmd_thread.join(timeout=5.0)

    if result_holder:
        kind, value = result_holder[0]
        if kind == "exit":
            final_status = "completed" if value == 0 else "failed"
            exit_code = value
        else:
            final_status = "failed"
            exit_code = None
    else:
        final_status = "failed"
        exit_code = None

    ended = timezone.now()
    session.refresh_from_db(fields=["status"])
    if session.status != "terminated":
        session.status = final_status
        session.exit_code = exit_code
        session.save(update_fields=["status", "exit_code", "updated_at"])

    turn.status = final_status
    turn.exit_code = exit_code
    turn.ended_at = ended
    turn.save(update_fields=["status", "exit_code", "ended_at"])
```

#### 2. Make `stream.py` export what `tasks.py` needs

`FLUSH_SIZE`, `TaggingQueueWriter`, `TaggedChunk`, `_SENTINEL`, `_build_turn_command` need to be importable. They currently are module-level in `stream.py` — just un-underscore the ones that are sensibly public:

- `_SENTINEL` → keep private; re-export as needed or inline.
- `_build_turn_command` → keep private to `stream.py`; tasks.py imports it (shared private module convention is fine within the same package).

Don't delete `run_session_background` yet — Phase 3 does that after the flip.

### Verification

- `make test` passes — Phase 2 adds the task but doesn't flip the caller yet.
- Manual: `python manage.py shell`, import `execute_turn`, call `.defer(...)` with fake data, check the Procrastinate jobs table has the row.

## Phase 3: Flip `run_turn` to defer

### Changes Required

#### 1. `src/agent_on_demand/session_service/turn.py`

Replace the whole file:

```python
"""Per-turn entry point: enqueue a Procrastinate task.

The web process no longer runs session execution. It creates the DB rows and
defers the work onto the worker service; the task body lives in
`session_service.tasks.execute_turn`.
"""

from __future__ import annotations

from sprites import Sprite

from agent_on_demand.models import AgentSession, SessionTurn
from agent_on_demand.session_service.tasks import execute_turn


def run_turn(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,  # kept in the signature for source-compat; only sprite_name is used
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Enqueue a task to execute this turn on the worker service."""
    execute_turn.defer(
        session_id=str(session.id),
        turn_id=turn.id,
        user_id=session.user_id,
        runtime_name=session.runtime,
        prompt=prompt,
        mode=mode,
        timeout=float(timeout),
    )
```

The `sprite` parameter is unused here — the task re-opens the handle via `resume_session(user, session.sprite_name)`. Keeping it in the signature avoids churning call sites in `views/sessions.py`. Consider dropping it in a follow-up once callers are updated.

#### 2. `src/agent_on_demand/stream.py`

Delete `run_session_background` and `_run_session_background_inner`. Keep `TaggingQueueWriter`, `TaggedChunk`, `_build_turn_command`, `_SENTINEL`, `FLUSH_SIZE`, and `stream_session_from_db`.

#### 3. No change to `views/sessions.py`

The view still calls `session_service.run_turn(session, turn, sprite, prompt, mode, timeout)`. The function's signature is unchanged; the body enqueues instead of threading.

### Verification

- Start the dev server (`make dev`) and the worker (`make worker`). `curl` a session create. Observe:
  - Web log shows the request, no execution-related output.
  - Worker log shows task pickup, SDK call, DB writes.
  - Session row transitions `pending → running → completed`.
  - SSE endpoint streams the expected events.
- If the worker is *not* running, the session stays `pending` indefinitely. This is the correct failure mode.

## Phase 4: Tests

### Changes Required

#### 1. `tests/conftest.py`

Replace the `threading.Thread` mock with Procrastinate's in-memory connector:

```python
import pytest
from django.test import Client
from procrastinate.testing import InMemoryConnector

from tests.fakes.sprite import RecordingSpritesClient


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def fake_sprites(mocker):
    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)
    return fake


@pytest.fixture
def inmemory_procrastinate(mocker):
    """Capture deferred Procrastinate tasks without executing them."""
    connector = InMemoryConnector()
    # procrastinate.contrib.django.app is a singleton; replace its connector
    from procrastinate.contrib.django import app as procrastinate_app
    mocker.patch.object(procrastinate_app, "connector", connector)
    return connector
```

Tests that used to assert `fake_sprites.last_sprite().writes == [...]` for the post-202 state can now assert `len(inmemory_procrastinate.jobs) == 1` with the expected payload. Tests that exercised the state machine directly call `execute_turn(session_id=..., ...)` instead.

#### 2. `tests/test_api.py`

Update the two patterns:

```python
# before: mocker.patch("agent_on_demand.session_service.turn.threading.Thread", ...)
# after:  use the inmemory_procrastinate fixture

def test_send_prompt_invokes_continue_mode(
    client, auth_headers, runtime_key, agent, user, fake_sprites, inmemory_procrastinate
):
    # ... setup session + turn 1 ...

    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "second"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    jobs = inmemory_procrastinate.jobs
    assert len(jobs) == 1
    job = list(jobs.values())[0]
    assert job["task_name"] == "execute_turn"
    assert job["args"]["mode"] == "continue"
    assert job["args"]["prompt"] == "second"
```

Update `test_run_session_background_persists_int_exit_code_on_exec_error` to call `execute_turn` directly (as a function, not a task) and assert DB state.

#### 3. `tests/test_tasks.py` (new)

```python
import pytest
from django.contrib.auth.models import User
from sprites import ExecError

from agent_on_demand.models import (
    AgentSession, APIKey, SessionTurn, UserRuntimeKey, UserSpritesKey,
)
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service.tasks import execute_turn


@pytest.mark.django_db
def test_execute_turn_marks_session_completed_on_success(user, mocker):
    # Standard fixtures + mocked sprite that returns exit 0.
    # Call execute_turn(...) directly.
    # Assert session.status == "completed", exit_code == 0, turn.ended_at set.
    ...


@pytest.mark.django_db
def test_execute_turn_marks_session_failed_on_exec_error(user, mocker):
    # Mocked sprite that raises ExecError(exit_code=2).
    # Call execute_turn(...) directly.
    # Assert session.status == "failed", exit_code == 2.
    ...


@pytest.mark.django_db
def test_execute_turn_preserves_terminated_status(user, mocker):
    # Race case: session is terminated mid-run. execute_turn finishes anyway
    # but must NOT clobber terminated → failed.
    ...


@pytest.mark.django_db
def test_execute_turn_writes_log_rows_in_bulk(user, mocker):
    # Feed N chunks through TaggingQueueWriter, assert AgentSessionLog rows exist.
    ...
```

These replace what the old test_api.py::test_run_session_background_persists_int_exit_code_on_exec_error covered, more cleanly and with fuller coverage.

### Verification

- `make test` — all passing. Target: no drop in assertion coverage.
- New test file surfaces the task's state-machine contract explicitly (which today is tested only implicitly via mocking `threading.Thread`).

## Phase 5: Cleanup (can stay in this PR or be a follow-up)

- Drop the unused `sprite` parameter from `run_turn` and its view callers. Touches `views/sessions.py` in two places.
- Update `.claude/skills/sprites.md` and `.claude/skills/agent-on-demand-api.md` to reflect the worker model.
- `CLAUDE.md` under "Where things live" adds:
  - `session_service/tasks.py` — Procrastinate task definitions
  - `worker` service alongside `web` in the deploy docs

## Risks

1. **Render's SIGTERM→SIGKILL window on Background Workers is unknown.** If short, in-flight tasks on a worker deploy get killed. Procrastinate's built-in retry handles this (task re-runs on next worker), but the Sprite may be left in an inconsistent state (already-executing, no log consumer). Concrete mitigation: make the task idempotent on the `running` side — if it starts and sees `turn.status == "running"` already, it could either bail out (trust the Sprite is done) or reset and rerun. Deferred to follow-up; ship with default behavior first.
2. **Worker service failure blocks all execution.** If the Procrastinate worker service fails to start or crashes persistently, tasks pile up. Web still accepts `POST /sessions` and returns `202`. Client sees `pending` forever until worker recovers. This is the correct behavior (no lost work) but operators need visibility — add a health-check `GET /health/worker` that queries the task table for stuck jobs. Can be follow-up.
3. **DB connection budget doubles.** Web + worker both pool against Postgres. On a `basic-256mb` Postgres plan that's probably fine, but monitor after deploy.
4. **Procrastinate migration is a schema change on the production DB.** Additive (new tables), not destructive, but still runs via `preDeployCommand`. Test on staging first.
5. **Local dev requires a second process.** `make worker` must be running or turns don't execute. Documented in README; `Makefile` adds the target.
6. **Task argument serialization.** We pass `session_id`, `turn_id`, `user_id`, `runtime_name`, `prompt`, `mode`, `timeout` — all primitives. If future additions need non-JSON-serializable values, look them up in the task body via the DB, don't pass them in args.

## Open Questions

- **Render's graceful-shutdown window on Background Workers.** Unknown. Ship with defaults; observe after first deploy.
- **Procrastinate concurrency config.** Default is 1 worker × 1 concurrent job. Probably fine to start; bump via `--concurrency N` flag if needed.
- **Failure retry policy.** Default is retry-forever with exponential backoff. For session turns, forever is probably wrong — a retry after 10 minutes may try to re-run against a Sprite that's been cleaned up. Configure `retry=RetryStrategy(max_attempts=3, wait=...)` or mark the task non-retriable at all. Decide during Phase 2.

## Related

- Research: [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md)
- Decision doc: [`thoughts/plans/2026-04-19-threading-architecture-decision.md`](/thoughts/plans/2026-04-19-threading-architecture-decision.md)
- Bug-fix baseline (must land first): [`thoughts/plans/2026-04-19-threading-bug-fixes.md`](/thoughts/plans/2026-04-19-threading-bug-fixes.md) / [PR #48](https://github.com/ravi-hq/agent-on-demand/pull/48)
- Procrastinate docs: https://procrastinate.readthedocs.io
