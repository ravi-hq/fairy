---
date: 2026-04-19T22:55:00-07:00
researcher: Claude Code (team-research skill)
git_commit: a2531b34248f49c6d9610cfbf62b34ec1058c123
branch: main
repository: ravi-hq/agent-on-demand
topic: "What are we doing with threading? Discomfort with spawning threads inside a web server"
tags: [research, team-research, threading, deployment, concurrency, background-work]
status: complete
method: agent-team
team_size: 4
tracks: [thread-inventory, deployment-surface, prior-art, alternatives]
last_updated: 2026-04-19
last_updated_by: Claude Code
---

# Research: Threading in the Web Server

**Date**: 2026-04-19T22:55-07:00
**Researcher**: Claude Code (team-research)
**Git Commit**: [`a2531b3`](https://github.com/ravi-hq/agent-on-demand/commit/a2531b34248f49c6d9610cfbf62b34ec1058c123)
**Branch**: `main`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (4 specialist researchers)

## Research Question

*"What are we doing with threading? I don't like spawning threads inside of web servers."*

## Summary

The discomfort is well-founded. The app currently spawns **two nested daemon threads per turn** (outer via `run_turn`, inner via `run_session_background`) to wrap a blocking Sprites WebSocket exec that can last up to 10 minutes. This is v1 scaffolding from `thoughts/plans/2026-04-16-session-based-execution.md` that was never revisited.

Three compounding operational problems are already present on `main`:

1. **Deploy-time data corruption.** Gunicorn's default 30 s graceful-shutdown timeout is **20× shorter** than the 600 s per-turn limit. Every deploy kills in-flight daemon threads mid-run; the Sprite keeps executing on the remote, and the DB row stays `status="running"` forever with no reconciliation.
2. **Capacity is 3 concurrent sessions, not by design.** Render's starter plan runs 3 sync Gunicorn workers (`2*CPU+1`). The SSE stream endpoint holds a worker for the full turn duration via a 500 ms sleep-poll loop (`stream.py`). Three active streams → server is full.
3. **Zombie executions.** The documented 5 s `cmd_thread.join(timeout=5.0)` window in `stream.py:152` can mark a session `failed` while the daemon thread is still running and the Sprite is still executing. Combined with `failed` being a resumable state, a client can trigger a second concurrent execution on the same Sprite.

**Short-term fix (low risk, no new infra):** replace the bare `threading.Thread(daemon=True)` spawn with a **bounded `ThreadPoolExecutor`** managed as a module-level singleton, with a proper `atexit` / Django signal shutdown hook that waits up to N seconds. Gives bounded capacity, deterministic shutdown semantics, and preserves the existing SSE-via-DB-polling design. Alongside this, two small-surface bug fixes that are orthogonal but cheap: (1) `close_old_connections()` at thread entry and `connection.close()` at exit (fixes an active DB connection leak on long turns, not just a latent one), and (2) fix Muddy Zone 8 by propagating a cancel via `threading.Event` + `future.cancel()` on the SDK's internal asyncio future — the SDK uses `asyncio.run_coroutine_threadsafe` internally, so the future is cancellable without any library change. Finally: Gunicorn's `--graceful-timeout` needs to be raised to match the 600 s turn limit, and SSE worker saturation needs a separate fix — either switch to Gunicorn `gthread` workers (`--worker-class=gthread --threads=N`), or replace the SSE endpoint with a client-polling `/sessions/{id}/logs?after={id}` endpoint. Neither of those is fixed by the executor alone.

**Medium-term fix (durable):** move session execution to an **out-of-process Postgres-backed worker** (Procrastinate is the cleanest fit for this Render + Postgres stack — no Redis tax, Postgres is already provisioned). Requires a new Render Background Worker service (~$7/mo). That unlocks deploys without losing in-flight work, proper retry semantics, and decouples long-running turns from the web workers' capacity.

**Not recommended:** Celery / RQ / Dramatiq (Redis adds ops cost for no win here), asyncio/ASGI rewrite (the Sprites SDK internally runs its own event loop anyway — migrating buys little and costs a lot).

## Research Tracks

### Track 1: Thread inventory + lifecycle
**Researcher**: thread-inventory-researcher
**Scope**: `src/agent_on_demand/` full sweep; `.venv/.../sprites/`

#### Findings

1. **Outer daemon thread** — [`session_service/turn.py:33-38`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/session_service/turn.py#L33-L38). Spawned by views on every turn (both `"run"` and `"continue"`). Target is `run_session_background`. `daemon=True`. HTTP caller returns 202 immediately.
2. **Inner daemon thread** — [`stream.py:126`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L126). Spawned *inside* the outer thread. Target is `_run_command` which calls `sprite.command(...).run()` — the blocking Sprites WebSocket exec. `daemon=True`.
3. **What `_run_command` does** — [`stream.py:91-117`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L91-L117). Runs the inline `bash -c` turn command, pipes stdout/stderr through `TaggingQueueWriter` onto a shared `queue.Queue`. Records `("exit", code)` or `("error", str)` into `result_holder`. Sentinels the queue in `finally`.
4. **Coordination model** — [`stream.py:129-164`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L129-L164). Outer thread drains the queue in batches of 20, writes `AgentSessionLog` rows, then joins the inner thread with a **5 s timeout**.
5. **Zombie window confirmed** — [`stream.py:152`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L152). Muddy Zone 8 from prior research. Timeout fires → `result_holder` empty → session marked `failed, exit_code=None`. Inner thread still alive, Sprite still executing, no kill mechanism. Combined with `failed` being resumable (Muddy Zone 2), a follow-up `POST /prompt` spawns a *second* execution on the same Sprite.
6. **ORM access from background threads without cleanup** — [`stream.py`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py). `session.save()`, `AgentSessionLog.objects.bulk_create()`, `turn.save()` run on the daemon thread. No `connection.close()` or `close_old_connections()` on thread exit. Latent connection-leak risk.
7. **Daemon thread on worker restart = orphans** — Both threads are `daemon=True`, so SIGTERM/SIGKILL kills them mid-flight. Sprite keeps running on the remote; session row stays `status="running"` with no reconciliation job.
8. **Only two thread spawns exist** — entire `src/` search. No `asyncio.create_task`, `ThreadPoolExecutor`, `Timer`. The threading surface is small but load-bearing.

### Track 2: Deployment surface
**Researcher**: deployment-researcher
**Scope**: `render.yaml`, `pyproject.toml`, `Makefile`, `config/settings.py`, `config/wsgi.py`

#### Findings

1. **Production**: [`render.yaml:18`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/render.yaml#L18) runs `uv run gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`. No `--workers`, `--threads`, `--timeout`, or `--max-requests` flags — pure defaults.
2. **Gunicorn defaults activated** — sync workers, `2*CPU+1` processes (Render starter = 1 CPU → 3 workers), **30 s graceful-shutdown timeout**, no voluntary worker recycling.
3. **Development**: [`Makefile:5`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/Makefile#L5) → `python manage.py runserver 0.0.0.0:8777`. Single-threaded Django dev server.
4. **Critical gap: 30 s shutdown vs. 600 s turn limit** — [`config/settings.py:96`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/config/settings.py#L96): `DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "600"))`. Every deploy/restart force-kills daemon threads after 30 s. No graceful drain of in-flight work.
5. **SSE endpoint pins a sync worker** — [`stream.py:180`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L180). `stream_session_from_db` runs `time.sleep(0.5)` in a loop for the full turn duration. Three concurrent streams = worker pool exhausted.
6. **Render only** — `render.yaml` + PostgreSQL `basic-256mb` web service `starter`. No Dockerfile, no Procfile, no Fly config, no compose file. No Background Worker service.
7. **Pure WSGI** — [`config/wsgi.py`](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/config/wsgi.py). No ASGI conversion, no `sync_to_async` wrapping, no Channels.

### Track 3: Prior art + decision history
**Researcher**: prior-art-researcher
**Scope**: `thoughts/plans/`, `thoughts/research/`, `git log --all`

#### Findings

1. **Decision origin** — [`thoughts/plans/2026-04-16-session-based-execution.md`](/thoughts/plans/2026-04-16-session-based-execution.md). Rationale for `threading.Thread(daemon=True)` was **explicitly "v1 simplicity, already using threads for streaming"**. Not a considered architectural choice — a deliberate deferral.
2. **Celery and ASGI explicitly rejected** — [`thoughts/research/2026-04-16-session-based-execution.md`](/thoughts/research/2026-04-16-session-based-execution.md). Direct quote: *"No Celery or task queue — background threads are sufficient for v1. No ASGI migration — WSGI handles this fine."*
3. **Muddy Zone 8 already flagged as "serious"** — [`thoughts/research/2026-04-18-sprites-script-setup.md`](/thoughts/research/2026-04-18-sprites-script-setup.md). The join-timeout zombie window is called out as the single most concerning interaction surfaced by that research, and was explicitly deferred again in `thoughts/plans/2026-04-19-session-service-api-refactor.md` under "What We're NOT Doing."
4. **State machine never tested** — [`thoughts/research/2026-04-18-session-backend-abstraction.md`](/thoughts/research/2026-04-18-session-backend-abstraction.md). The `pending → running → completed` state machine in `run_session_background` is mocked out in every unit test via `patch("session_service.turn.threading.Thread")`. No test exercises the real thread lifecycle.
5. **No commits touch threading meaningfully** — `git log --oneline --all | head -60` has zero commits matching `threading`, `celery`, `async`, `worker`, or `daemon` in their messages. The pattern has been unchanged since the session-based-execution feature landed.

### Track 4: Alternatives + migration paths
**Researcher**: alternatives-researcher
**Scope**: Library survey + `render.yaml` + `.venv` inspection

#### Findings

1. **The Sprites SDK is not really sync at the bottom** — `.venv/.../sprites/exec.py` → `_run_sync()` → `sprites.loop.run_sync()` → `asyncio.run_coroutine_threadsafe(coro, persistent_loop)`. The SDK has its **own singleton asyncio event loop on a background thread**; `cmd.run()` just blocks the calling thread via `future.result(timeout=...)`. Implication: the "sync-only SDK" framing isn't strictly true. An ASGI view calling `sync_to_async(cmd.run)()` would work — but the SDK's own loop still adds a layer.
2. **Django 6.x has a task framework skeleton, no worker backend bundled** — `.venv/.../django/tasks/` ships `ImmediateBackend` (in-process synchronous) and `DummyBackend`. No DB-backed worker backend is included. `django-tasks` as a Django-native direction is promising but the `django-db-tasks-backend` DB driver is young.
3. **Render deployment constraint** — Only a `web` service + Postgres in `render.yaml`. No Redis. A Postgres-backed broker (Procrastinate, django-tasks) avoids adding Redis; a worker-based broker (Celery, RQ, Dramatiq) would add ~$7/mo for Redis plus another Render Background Worker service.

#### Ranked shortlist

| Option | Broker | Sync-SDK fit | Streaming story | Django fit | Render fit | Migration cost |
|---|---|---|---|---|---|---|
| **ThreadPoolExecutor (bounded)** | none | native | unchanged (DB polling) | manual | works on current plan | **minimal** |
| **Procrastinate (Postgres broker)** | Postgres (already have) | good | unchanged | `django-procrastinate` | add BG worker service | medium |
| **django-tasks (DB backend, third-party)** | Postgres | good | unchanged | excellent | add BG worker service | medium |
| **Celery / RQ / Dramatiq** | Redis | good | unchanged | mature | add Redis + BG worker | high |
| **asyncio + ASGI** | none | awkward double-bridge | async SSE possible | requires ASGI | uvicorn worker needed | high |

**Recommendation:** ThreadPoolExecutor short-term (costs nothing, immediately caps capacity and enables deterministic shutdown). Procrastinate medium-term if durability is required.

## Cross-Track Discoveries

These are the findings that emerged from researchers messaging each other — not visible from any single track.

**1. The 30 s gunicorn grace period makes Muddy Zone 8 moot — and worse.**
Track 1 flagged the 5 s `cmd_thread.join(timeout=5.0)` as the window where a session gets marked `failed` while the Sprite is still executing. Track 2 added the crucial context: gunicorn's default graceful-shutdown is 30 s. So on every deploy during an in-flight turn, the sequence is: SIGTERM → request thread tries to finish → 30 s later SIGKILL → daemon threads die → outer thread never got to the 5 s join at all → session stays `running` forever with zero DB transition. The Muddy Zone 8 zombie becomes a "session row stuck in running" orphan, which is strictly worse than the already-documented "two concurrent executions" hazard.

**2. Worker capacity is 3, not 1.**
Track 1 initially assumed single-worker capacity. Track 2 corrected: Render starter → 3 sync workers via Gunicorn's `2*CPU+1` default. This changes the math but not the conclusion — three concurrent streaming SSE clients still saturate the pool because each SSE response holds a worker for the turn's full duration.

**3. Prior art explicitly punted — this research is the trigger for reconsidering.**
Track 3 found the "Celery rejected for v1 simplicity" decision in the 2026-04-16 plan, and the subsequent "not fixing Muddy Zone 8" in the 2026-04-19 plan. Neither document defined a trigger for re-evaluation. Track 4's survey confirms nothing in the codebase has changed the calculus yet — but the **accumulation** of the three operational problems (deploy orphans + capacity pinning + zombie executions) has crossed the threshold, and the ecosystem now offers Postgres-backed options (Procrastinate) that weren't part of the original "Celery vs. threads" framing.

**4. The SDK's secret async loop means ASGI is technically feasible but buys nothing.**
Track 4 discovered `sprite.command().run()` is internally `asyncio.run_coroutine_threadsafe` against a singleton loop. This means the migration barrier to ASGI views + `sync_to_async` is lower than one would expect from the blocking call surface. But the payoff is also lower than one would hope — you'd still be bridging two event loops, the existing SSE-via-DB pattern wouldn't become materially simpler, and the core problems (worker capacity, deploy shutdown, zombies) aren't caused by sync-ness. Track 4's recommendation to skip ASGI is correct.

**5. Muddy Zone 8 is fixable without a worker migration — it's orthogonal to executor vs. thread.**
Post-synthesis sync between Tracks 3 and 4 sharpened this: neither ThreadPoolExecutor nor Procrastinate/django-tasks fixes the 5 s join timeout zombie on their own. Three in-place options: (a) guard `send_prompt` so `failed` sessions can't be resumed (blunts the "two executions on one Sprite" hazard without touching threads), (b) propagate cancellation to the Sprite via the SDK's already-cancellable asyncio future + a `threading.Event` set from the outer thread, or (c) simply extend the join timeout past the SDK's own command timeout (so the thread either finishes or the SDK raises). (b) is the right fix long-term; (a) or (c) are one-liner bridges.

**6. SSE saturation is orthogonal to execution-thread concurrency.**
Track 4 flagged this post-synthesis: even if execution moves to a bounded pool or a worker process, the SSE streaming endpoint still pins a sync Gunicorn worker per active client via `time.sleep(0.5)` (`stream.py:180`). Three concurrent SSE clients = server full, regardless of how executions run. Fixes: Gunicorn `gthread` worker class (`--worker-class=gthread --threads=N`, lowest change), or replace the SSE endpoint with a client-polling `/sessions/{id}/logs?after={id}` endpoint (breaks the SSE API contract, but pure request/response — no worker pinning). ASGI+uvicorn is the third option and the one with the highest cost.

**7. `connection.close()` absence is an active leak, not latent.**
Track 4 follow-up: since Gunicorn runs without `--max-requests`, workers never recycle voluntarily. Each long turn (up to 600 s) leaves a DB connection held by the daemon thread; the connection never closes until the worker itself dies. On a busy instance, this steadily consumes the Postgres connection budget. The fix is `close_old_connections()` at `run_session_background` entry + `connection.close()` at exit — Django's documented pattern for long-lived threads.

## Code References

| File | Tracks | Findings | Link |
|---|---|---|---|
| `src/agent_on_demand/session_service/turn.py:33-38` | 1 | Outer daemon thread spawn | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/session_service/turn.py#L33-L38) |
| `src/agent_on_demand/stream.py:126` | 1 | Inner daemon thread spawn | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L126) |
| `src/agent_on_demand/stream.py:152` | 1, 3 | 5 s join timeout — zombie window (Muddy Zone 8) | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L152) |
| `src/agent_on_demand/stream.py:91-117` | 1 | `_run_command` body — SDK call + queue writer | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L91-L117) |
| `src/agent_on_demand/stream.py:129-164` | 1 | Outer thread queue drain + DB writes + join | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L129-L164) |
| `src/agent_on_demand/stream.py:180` | 2 | SSE `time.sleep(0.5)` poll loop pinning workers | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/agent_on_demand/stream.py#L180) |
| `render.yaml:18` | 2 | Gunicorn invocation with zero flags | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/render.yaml#L18) |
| `src/config/settings.py:96` | 2 | `DEFAULT_TIMEOUT = 600` vs. 30 s gunicorn grace | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/a2531b3/src/config/settings.py#L96) |

## Architecture Insights

- **The SSE endpoint and the execution thread are already decoupled** via the `AgentSessionLog` table — streaming reads from DB, writes happen in the thread. This is the single most useful pre-existing property: any migration to an out-of-process worker inherits an already-working streaming story without touching clients.
- **The pattern is "fire-and-forget 202 + reconnect for SSE"** — the HTTP request doesn't need the thread to complete. This is the right pattern for queued/async execution, just implemented with threads for now.
- **The threading surface is small** — two spawns, both in files we own. Migration to a pool or a queue is mechanically small; the risk is operational (broker, worker deploy unit, graceful handoff), not code-shaped.
- **Daemon threads + no ORM cleanup is a latent connection leak** — Django's advice is to `close_old_connections()` when a thread finishes. Today the daemon thread's DB connection just dangles until gunicorn recycles the worker (which it doesn't, because no `--max-requests`).

## Historical Context

- [`thoughts/plans/2026-04-16-session-based-execution.md`](/thoughts/plans/2026-04-16-session-based-execution.md) — Original decision to use daemon threads. Rationale: v1 simplicity. Celery + ASGI explicitly deferred.
- [`thoughts/research/2026-04-18-sprites-script-setup.md`](/thoughts/research/2026-04-18-sprites-script-setup.md) — First flagged Muddy Zone 8 (the 5 s join zombie) as the single most concerning interaction in the execution model.
- [`thoughts/research/2026-04-18-session-backend-abstraction.md`](/thoughts/research/2026-04-18-session-backend-abstraction.md) — Noted the state machine is never tested because `threading.Thread` is mocked in every unit test.
- [`thoughts/plans/2026-04-19-session-service-api-refactor.md`](/thoughts/plans/2026-04-19-session-service-api-refactor.md) — Most recent plan; explicitly deferred fixing the zombie window and threading model.

## Recommended Actions (ranked)

These are grouped by **where they fix a real problem** — several are genuinely independent and can land as separate small PRs.

**Group A: bug fixes, do these regardless of larger architecture choices.**

1. **Add `close_old_connections()` at `run_session_background` entry, `connection.close()` at exit.** Fixes an active DB connection leak (not latent — workers don't recycle because there's no `--max-requests`). Django's documented pattern for long-lived threads. 1-line change.
2. **Fix Muddy Zone 8 (zombie window).** Preferred: pass a `threading.Event` into `_run_command`, set it from the outer thread on join timeout, and call `future.cancel()` on the SDK's internal asyncio future (already cancellable via `run_coroutine_threadsafe`). Cheaper bridge: guard `send_prompt` so `failed` sessions can't be resumed — blunts the "two executions on one Sprite" hazard without touching the thread code.
3. **Raise Gunicorn's graceful timeout + add `--max-requests`.** Edit `render.yaml` → `gunicorn ... --graceful-timeout 650 --timeout 700 --workers 3 --max-requests 1000 --max-requests-jitter 50`. Matches the 600 s turn limit so deploys don't kill in-flight turns. Also enables voluntary worker recycling, which incidentally reclaims leaked DB connections if #1 doesn't ship first.

**Group B: capacity + shutdown — pick one, they conflict less than they look.**

4. **Replace raw daemon threads with a bounded `ThreadPoolExecutor`.** Module-level singleton in `session_service/turn.py`, `shutdown(wait=True, cancel_futures=False)` wired to `atexit` + Django's `request_finished` teardown. Picks a capacity cap (10? 50?), makes shutdown semantics explicit, gives a natural hook for state-transition callbacks. **Still in-process — does not survive a deploy.** Pair with #3.
5. **Switch Gunicorn to `gthread` workers for SSE concurrency.** Add `--worker-class=gthread --threads=8`. Each sync worker process gets a thread pool so the SSE `sleep(0.5)` poll loop no longer pins an entire worker. Low-risk, no code change. Orthogonal to #4 — if executions are still daemon threads, this only helps the streaming side. If executions move to the pool in #4, gthread helps streaming; if executions move out-of-process (#6), gthread also helps streaming.
6. **Evaluate Procrastinate as a full out-of-process move.** `render.yaml` adds a `worker` service, `pyproject.toml` adds the dep, a migration creates the broker schema, `run_turn` switches from `thread.start()` to `task.enqueue()`. Clients unchanged (SSE still reads DB), deploys can complete without dropping turns, retries + DLQ are natural. The "fix deploy-time data loss" path — but overkill if the actual deploy-loss rate is low.

**Not recommended.**

- **ASGI + uvicorn.** The SDK's own event loop makes this technically feasible with `sync_to_async` but there's no material payoff — the real problems (capacity, shutdown, zombies) aren't caused by sync-ness.
- **Celery / RQ / Dramatiq.** Adds a Redis dependency for no advantage over Procrastinate when Postgres is already provisioned.
- **Replacing SSE with client polling.** Was considered — it's a cleaner fit for WSGI, but breaks the existing client API contract for questionable gain over `gthread` mode.

## Open Questions

- **What's the actual observed deploy-loss rate in production?** The theoretical risk is "every deploy during an in-flight turn." Need metrics (turn success rate vs. deploy events) to prioritize 1–5 above.
- **Is there appetite for a $7/mo Background Worker + the coordination complexity it brings, or is the preference to stay single-service?** This gates item 5.
- **Should `failed` sessions be non-resumable?** Muddy Zone 2 (from the 2026-04-18 research) — currently they are resumable, which is what makes the zombie window into a two-executions-on-one-Sprite hazard. Flipping this would blunt the zombie risk independently of the threading model.
- **Does Render offer a longer graceful-shutdown window on paid plans?** Worth a check before committing to a graceful-timeout config change.

## Related Research

- [`thoughts/research/2026-04-18-sprites-script-setup.md`](/thoughts/research/2026-04-18-sprites-script-setup.md) — Execution model deep-dive; origin of Muddy Zone 8.
- [`thoughts/research/2026-04-18-session-backend-abstraction.md`](/thoughts/research/2026-04-18-session-backend-abstraction.md) — What a backend-abstraction layer would look like and what currently isn't tested.
- [`thoughts/research/2026-04-16-session-based-execution.md`](/thoughts/research/2026-04-16-session-based-execution.md) — Original decision doc.
