# Threading Bug Fixes — Implementation Plan

## Overview

Three small, independent fixes to the session-execution threading code. Each stands on its own; none commits to a larger architectural direction. These are the "Group A" items from [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md) — active bugs that deserve fixing before we decide where threading goes next.

No DB migration. No new dependencies. No API surface changes.

## Research Summary

Backed by [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md).

- **Bug 1 — Active DB connection leak.** `run_session_background` writes to the ORM from a daemon thread and never closes the connection on exit. Gunicorn runs without `--max-requests`, so workers never recycle voluntarily; the leaked connection is held for the worker's lifetime.
- **Bug 2 — Muddy Zone 8 zombie window.** `stream.py:152` has a 5 s `cmd_thread.join(timeout=5.0)` that marks the session `failed` while the inner daemon thread is still running and the Sprite is still executing. Combined with `failed` being a resumable state (Muddy Zone 2), a follow-up `POST /prompt` spawns a *second* execution on the same Sprite.
- **Bug 3 — Gunicorn 30 s graceful timeout vs. 600 s turn timeout.** Every deploy during an in-flight turn force-kills the daemon thread at the 30 s mark; the Sprite keeps executing; the DB row stays `status="running"` forever.

## Current State Analysis

- Two nested `threading.Thread(daemon=True)` spawns per turn: `session_service/turn.py:33-38` (outer) and `stream.py:126` (inner).
- Zero ORM connection cleanup anywhere in the thread body.
- `render.yaml:18`: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT` — no flags, pure defaults.
- `config/settings.py:96`: `DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "600"))`.
- `send_prompt` at `views/sessions.py` blocks only on `running` and `terminated`, so `failed` is resumable.

## Desired End State

1. Daemon threads doing ORM work call `close_old_connections()` at entry and exit.
2. The zombie window is closed. Either: (a) `failed` sessions can't be resumed, (b) cancellation propagates to the Sprite, or (c) both.
3. Gunicorn's `--graceful-timeout` accommodates the real turn limit; workers recycle voluntarily so leaked state can't accumulate indefinitely.

### Verification

- `make lint && make fmt` clean.
- `make test` passes. New unit tests cover: (a) `close_old_connections` called in thread entry/exit, (b) a follow-up `POST /prompt` on a `failed` session returns 409.
- Manual smoke: run an agent turn locally (`make dev`), verify a real DB connection closes on turn completion via `django.db.connections[DEFAULT].queries` inspection.
- `AOD_API_TOKEN=… make test-e2e-fast` passes.

## What We're NOT Doing

- Not replacing the daemon-thread model with a `ThreadPoolExecutor`, Procrastinate worker, ASGI, or any other execution-model change. Those decisions are the subject of [`thoughts/plans/2026-04-19-threading-architecture-decision.md`](/thoughts/plans/2026-04-19-threading-architecture-decision.md), which should land before any of those get implemented.
- Not fixing SSE worker-saturation (`time.sleep(0.5)` poll loop pinning workers). That's architectural — covered in the decision plan.
- Not adding structured failure telemetry beyond what already exists.
- Not changing `DEFAULT_TIMEOUT` or any turn-level semantics.

## Implementation Approach

Three independent changes. Can land as one PR (recommended — the review surface is small) or three stacked PRs if the team prefers smaller merges.

1. Close DB connections in the thread body (`stream.py`).
2. Block `send_prompt` on `failed` status + add a test.
3. Update `render.yaml` with graceful-timeout + max-requests.

Optional follow-up: propagate cancel to the Sprite's asyncio future on join timeout. Not required for this PR — the `failed`-is-terminal change makes the zombie window non-exploitable on its own. See "Open Follow-up" at the bottom.

## File Ownership Map

| File | Change | Notes |
|------|--------|-------|
| `src/agent_on_demand/stream.py` | modify | Add `close_old_connections()` at `run_session_background` entry and in a `finally` block at exit. |
| `src/agent_on_demand/views/sessions.py` | modify | `send_prompt`: reject `failed` with 409 alongside `running` and `terminated`. |
| `render.yaml` | modify | Add `--graceful-timeout 650 --timeout 700 --workers 3 --max-requests 1000 --max-requests-jitter 50` to the gunicorn invocation. |
| `tests/test_api.py` | modify | Add `test_send_prompt_rejects_failed_session`. |
| `tests/test_stream.py` (create, or add to existing) | create/modify | Assert `close_old_connections` is invoked on thread entry and exit. |

## Phase 1: DB connection cleanup

### Changes Required

#### 1. `src/agent_on_demand/stream.py` — wrap the thread body

Import `close_old_connections` at the top:

```python
from django.db import close_old_connections
```

Call it at the start of `run_session_background` and in a `finally` block:

```python
def run_session_background(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    runtime: RuntimeConfig,
    prompt: str,
    mode: str,
    timeout: float,
):
    # Django's recommended pattern for long-lived threads: close any
    # stale connections on entry (in case this thread inherits one from
    # the request handler) and close again on exit so we don't hold a
    # connection for the worker's full lifetime.
    close_old_connections()
    try:
        # ... existing body (output_q setup, _run_command inner thread,
        # queue drain loop, state transitions) ...
    finally:
        close_old_connections()
```

Single wrap. Placement matters: entry-side catches any stale connection inherited from the request thread; exit-side releases the connection this thread opened for the DB writes.

#### 2. `tests/test_stream.py` — new unit test

Either create `tests/test_stream.py` or add a class to an existing file:

```python
@pytest.mark.django_db
def test_run_session_background_closes_db_connections(user, mocker):
    from sprites import ExecError

    from agent_on_demand.models import SessionTurn
    from agent_on_demand.runtimes import RUNTIMES
    from agent_on_demand.stream import run_session_background

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", status="pending"
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="t", status="pending"
    )

    mock_sprite = mocker.MagicMock()
    mock_cmd = mocker.MagicMock()
    mock_cmd.run.side_effect = ExecError("exit status 0", exit_code=0)
    mock_sprite.command.return_value = mock_cmd

    close_spy = mocker.spy(
        __import__("django.db", fromlist=["close_old_connections"]),
        "close_old_connections",
    )

    run_session_background(
        session, turn, mock_sprite, RUNTIMES["claude"], "hi", "run", timeout=10.0
    )

    # Called at entry and at exit (finally).
    assert close_spy.call_count >= 2
```

The `>= 2` rather than `== 2` is defensive — if the code path inadvertently calls it an extra time elsewhere, the test still passes. The semantic guarantee is "at least entry + exit."

## Phase 2: Close the zombie window via `failed`-is-terminal

### Changes Required

#### 1. `src/agent_on_demand/views/sessions.py` — reject resume of `failed`

Find the existing block in `send_prompt`:

```python
if session.status == "running":
    return JsonResponse({"detail": "Session is already running"}, status=409)

if session.status == "terminated":
    return JsonResponse({"detail": "Session has been terminated"}, status=409)
```

Add a `failed` branch:

```python
if session.status == "failed":
    return JsonResponse(
        {"detail": "Session has failed and cannot be resumed. Start a new session."},
        status=409,
    )
```

Do the same in the locked re-check inside the `transaction.atomic()` block of `send_prompt`.

#### 2. `tests/test_api.py` — regression test

```python
@pytest.mark.django_db
def test_send_prompt_rejects_failed_session(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test",
        sprite_name="sprite-x", status="failed", exit_code=1,
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "retry"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"].lower()
```

### Why this, not the cancel-via-future fix?

The cleaner long-term fix is "on join timeout, cancel the SDK's internal asyncio future via `future.cancel()`" (see the research doc, cross-track discovery #5). But that touches the Sprites SDK internals and needs real-Sprite validation. The `failed`-is-terminal change blunts the actual hazard (two executions on one Sprite) with one line of code and zero new surface. The cancel-via-future fix can land later as a separate PR.

This is explicitly accepting that a *single* failed turn can still leave the Sprite executing for up to its timeout — it just can't cause a second turn to collide with it. Acceptable tradeoff.

## Phase 3: Raise Gunicorn graceful timeout + add max-requests

### Changes Required

#### 1. `render.yaml` — extend the gunicorn invocation

Find:

```yaml
startCommand: uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

Replace with:

```yaml
startCommand: >
  uv run gunicorn config.wsgi:application
  --bind 0.0.0.0:$PORT
  --workers 3
  --graceful-timeout 650
  --timeout 700
  --max-requests 1000
  --max-requests-jitter 50
```

Explanation of each flag:

- `--workers 3`: explicit, matches Render starter's `2*CPU+1` default. Makes the contract visible.
- `--graceful-timeout 650`: 50 s of headroom above the 600 s `DEFAULT_TIMEOUT`. On SIGTERM, Gunicorn waits this long before force-killing workers — long enough for an in-flight turn to finish.
- `--timeout 700`: worker silence timeout. Above graceful-timeout so Gunicorn doesn't self-kill a worker that's legitimately running a long turn.
- `--max-requests 1000 --max-requests-jitter 50`: voluntarily recycle workers after ~1000 requests (jittered). Caps leaked-state accumulation — if Phase 1 misses a connection path, this still bounds the damage.

**Render-specific caveat to verify:** Render's platform-level deploy timeout may be *lower* than 650 s on the starter plan. If so, this config is aspirational and we need to either (a) upgrade the plan, (b) accept deploy kills but rely on Phase 2 to prevent the worst outcome. The `--graceful-timeout` should match whatever Render gives us; if Render is 30 s platform-level, setting Gunicorn to 650 s gains nothing.

**How to verify:** before shipping, check Render's docs / dashboard for the actual SIGTERM→SIGKILL window on the starter plan. If it's < 650 s, pick the largest plan-compatible value and document the mismatch in the PR.

### Verification

- `render.yaml` parses clean (`yamllint` or just `python -c "import yaml; yaml.safe_load(open('render.yaml'))"`).
- Deploy to staging, trigger a deploy during an in-flight agent turn, confirm the turn completes rather than orphaning.

## Risks

- **Render graceful-timeout ceiling unknown.** The Phase 3 config assumes Render lets Gunicorn take 650 s. If Render caps lower, Phase 3 is a no-op and the real fix is Plan 2's "out-of-process worker" track.
- **`failed`-is-terminal is a behavior change visible to clients.** Today a client can retry a failed session and get a 202. After Phase 2 they get a 409. Callers who retry on failure need to start a new session. Worth a note in CHANGELOG / release notes.
- **`close_old_connections()` inside the outer thread doesn't reach the inner `_run_command` thread.** The inner thread doesn't touch the ORM currently, so this is fine today — but if anyone ever adds DB writes inside `_run_command`, they'd need their own `close_old_connections()`. Document this in a comment at the `_run_command` definition so the next person notices.

## Open Follow-up

**Cancel via future in a separate PR.** The structural fix to the zombie window — propagating cancellation to the SDK's internal asyncio future — is worth doing, but it (a) touches SDK internals that need real-Sprite validation, and (b) is a meaningfully larger surface than the three fixes above. Proposed approach for the follow-up:

1. Pass a `threading.Event` into `_run_command`.
2. On join timeout, `set()` the event from the outer thread.
3. `_run_command` watches the event between queue writes and calls `future.cancel()` on the `asyncio.run_coroutine_threadsafe` future (retrievable from the SDK's internal `Command` object — need to confirm the SDK exposes it).

That's a full spike on the Sprites SDK and is out of scope here.

## Related

- Research: [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md) — the full investigation that surfaced these bugs.
- Architectural decision (next): [`thoughts/plans/2026-04-19-threading-architecture-decision.md`](/thoughts/plans/2026-04-19-threading-architecture-decision.md) — what we do about the daemon threads themselves, after the bugs are fixed.
