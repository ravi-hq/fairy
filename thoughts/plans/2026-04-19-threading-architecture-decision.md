# Threading Architecture — Decision Plan

## What this document is

This is a **decision plan**, not an implementation plan. Its job is to surface the real contenders for how session execution should work, call out what makes them differ, and identify the small number of open questions whose answers determine the pick. Once we land on a direction, a separate implementation plan follows.

The research behind this is [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md). The baseline bug-fix work (which must land first) is [`thoughts/plans/2026-04-19-threading-bug-fixes.md`](/thoughts/plans/2026-04-19-threading-bug-fixes.md).

## The problem we're solving

Today, every turn spawns **two nested daemon threads** inside the Gunicorn worker process. This has three structural consequences that the bug-fix plan does not address:

1. **No durability across deploys.** Daemon threads die with the worker. Even with a 650 s graceful timeout, every deploy during an in-flight turn is a gamble.
2. **Capacity cap = 3 concurrent streaming sessions.** Render starter → 3 sync Gunicorn workers, and the SSE endpoint pins a worker for the turn's full duration via `time.sleep(0.5)` polling. Execution threads are a separate concurrency axis; the SSE pin is independent.
3. **Thread lifecycle is load-bearing but invisible.** The state machine in `run_session_background` is never tested — every unit test mocks `threading.Thread`. What actually happens on a thread panic, a queue overflow, or a shutdown mid-drain is documented only by inspection of the code.

The user's original framing — *"maybe consider not needing to spawn them"* — is the right reframe. This document evaluates **three contenders**, only one of which keeps the "spawn a background thread per turn" pattern.

## Contenders

### Contender A — Keep threads, tame them
*"Bounded `ThreadPoolExecutor` + `gthread` Gunicorn workers + cancel-via-future."*

**What changes:**
- `session_service/turn.py`: replace the bare `threading.Thread(daemon=True)` spawn with a module-level `ThreadPoolExecutor(max_workers=N)`. Wire `shutdown(wait=True, cancel_futures=False)` to `atexit` + Django's `request_finished`.
- `render.yaml`: add `--worker-class=gthread --threads=8` to the Gunicorn invocation. SSE endpoints no longer pin an entire worker process.
- `stream.py`: the inner `cmd_thread` stays, but pass a `threading.Event` into it. On outer-thread join timeout, set the event and call `future.cancel()` on the SDK's internal asyncio future (which is accessible via `cmd._future` or similar — needs a small spike on the Sprites SDK to confirm).
- The `run_session_background` shape stays — same queue drain, same DB writes, same SSE replay pattern.

**What it buys:**
- Bounded capacity (no more unlimited daemon spawning).
- Deterministic shutdown semantics.
- SSE concurrency = workers × threads instead of workers.
- Actual cancellation of runaway turns.

**What it doesn't fix:**
- Deploys still kill in-flight turns. `graceful-timeout` helps but is ultimately bounded by Render's platform SIGKILL window.
- Still two nested threads per turn, just accounted for.
- Turn state reconciliation (what happens to a turn whose worker died mid-flight?) is still "nothing — the row stays `running` forever."

**Cost:**
- Small code change (~50 lines touched), no new dependencies, no new Render services.
- One real operational thing to understand: `gthread` workers use the GIL differently and have slightly different memory behavior. Not a hazard, just a knob change.

**Time estimate:** 1–2 days of focused work.

---

### Contender B — Don't spawn threads at all
*"Streaming POST response; turn runs inline on the HTTP thread via gthread workers."*

**What changes:**
- `POST /sessions` stops returning 202. Instead, it returns `200 OK` with `Content-Type: text/event-stream` and streams the turn's output directly on the response body. The request handler *is* the runner — no background thread.
- The SDK call (`sprite.command(...).run()`) runs on the Gunicorn worker thread. With `--worker-class=gthread --threads=8`, a single worker can hold 8 concurrent turns.
- The inner `_run_command` thread goes away entirely — the SDK's own event loop thread (which already exists in `sprites.loop`) does the WebSocket work; the Gunicorn thread blocks waiting for it via `asyncio.run_coroutine_threadsafe().result()`, which is what `cmd.run()` already does.
- `run_session_background` is deleted. `TaggingQueueWriter` hooks into the HTTP response stream instead of an internal queue.
- `GET /sessions/{id}/stream` still exists but becomes *replay-only* — tails `AgentSessionLog` for observers who missed the live stream.
- `POST /sessions/{id}/prompt` (continue turns) works the same way — streams directly in the response.
- DB writes still happen during the turn (per-log-chunk inserts + state transitions), but from the request handler thread, not a daemon thread. No `close_old_connections()` needed because Django's per-request connection lifecycle already handles it.

**What it buys:**
- **Zero daemon threads in the web process.** The user's stated preference, taken seriously.
- Simplest possible concurrency model: "one turn per HTTP thread, capacity = workers × threads."
- No Muddy Zone 8 zombie window — there's no `join(timeout)` to time out. The SDK call either completes or the client disconnects (which kills the worker thread, which kills the SDK call).
- No state-machine ambiguity. On worker crash, the HTTP connection drops; the client knows; the DB row transition fails naturally.
- Simpler testing — the state machine is exercised by ordinary request tests.

**What it doesn't fix:**
- Deploys still kill in-flight turns. Client sees a dropped connection mid-stream; can retry by issuing a new POST.
- Turn state reconciliation still needs thought — a mid-turn SIGKILL leaves the DB row `running`. Need a reaper or a "mark orphaned on startup" hook.

**Cost:**
- Meaningful API contract change. POST now blocks for the turn's duration (up to 10 minutes). Any client that expected a 202 with a stream URL needs updating. Internal caller only? External? Need to check.
- Refactor of `stream.py` and `turn.py` — maybe ~150 lines touched, but most of it is deletion.
- The streaming-POST pattern is less common than 202+SSE-reconnect; some HTTP proxies have issues with long-lived streaming POST bodies. Render specifically needs to be verified.
- Reversing direction (going back to background threads) after shipping this is non-trivial.

**Time estimate:** 3–5 days. Refactor + client-side updates + verification of the streaming-POST path through Render's proxy layer.

---

### Contender C — Out-of-process worker
*"Procrastinate (Postgres broker) + Render Background Worker service."*

**What changes:**
- New `worker` entry in `render.yaml` pointing at a `procrastinate worker` invocation.
- `pyproject.toml` gains `procrastinate[django]` (or similar).
- New Django app / migrations for Procrastinate's task tables in the existing Postgres.
- `session_service/turn.py`: `run_turn` becomes `task_run_turn.defer(session_id=..., prompt=...)`. Returns immediately. HTTP returns 202 as before.
- `stream.py` `run_session_background` moves to a Procrastinate task function. Same body, but runs in the worker process.
- The SSE endpoint stays unchanged — still tails `AgentSessionLog`.
- On-deploy: the worker service restarts independently of the web service. In-flight tasks survive if Procrastinate's retry/recovery semantics are wired up (they are, built-in).

**What it buys:**
- **Real durability across deploys.** A web deploy doesn't touch worker state. A worker deploy can drain in-flight tasks gracefully (configurable timeout). Orphaned tasks can be retried.
- Clean separation: web workers for HTTP, background workers for SDK calls.
- Natural home for retry logic, DLQ, scheduled cleanup jobs.
- Capacity is tunable independently of HTTP capacity.

**What it doesn't fix:**
- SSE saturation on the web side (still need `gthread` or equivalent). That's orthogonal to where execution runs.
- The fundamental "long-running tasks are hard" operational weight — now there are two services to monitor instead of one.

**Cost:**
- **New Render Background Worker service** (~$7/mo on starter). Small $ cost but a real ops decision — more surface to monitor, deploy, and debug.
- New dependency (Procrastinate) with its own version cadence and learning curve.
- Migration: Procrastinate's schema migration runs against our Postgres. Low risk (it's additive tables), but real.
- Rollback harder if something goes wrong — the worker becomes load-bearing immediately.

**Time estimate:** 5–8 days. Infrastructure setup + task function port + retry semantics + CHANGELOG.

## Comparison

| Criterion | A: Thread pool + gthread | B: Streaming POST | C: Procrastinate worker |
|---|---|---|---|
| Daemon threads in web process | Yes, bounded | **No** | **No** |
| Survives deploy of web service | No | No | **Yes** |
| Fixes SSE saturation | Yes (gthread) | Yes (gthread) | Partial (needs gthread still) |
| Fixes zombie window | Yes (via future.cancel) | **N/A — no join timeout** | **N/A — worker owns lifecycle** |
| API contract change | No | **Yes — POST blocks** | No |
| New infra | No | No | **Yes — Render Background Worker** |
| New dependency | No | No | Procrastinate |
| Reversible | Cheap | Expensive | Cheap (can re-spawn threads) |
| Time estimate | 1–2 days | 3–5 days | 5–8 days |
| Cost | $0 | $0 | ~$7/mo |

## Recommendation (subject to the open questions below)

**First choice: Contender B (streaming POST).** It takes the user's "maybe not spawn them at all" framing seriously. The API change is the main cost, and that cost is inversely proportional to how young this project is — now is the cheapest moment to make it. The operational surface shrinks rather than grows.

**Second choice: Contender A (thread pool + gthread).** If the streaming-POST contract is a hard no — because external clients already depend on the 202+SSE-reconnect pattern, or because Render's proxy layer mishandles long streaming POSTs — this is the sane incremental path. Low risk, keeps threads but makes them accountable.

**Not first choice: Contender C (Procrastinate worker).** Right answer if durability-across-deploys is the most important property. Not first choice because the ops burden is real and the user's framing was "not spawn them," which C achieves but at the cost of more moving parts than B.

## Open questions (answer these before picking)

These are the questions whose answers change the ranking.

1. **Does Render's HTTP proxy handle long-lived streaming POST responses well?** Specifically: does it honor `Transfer-Encoding: chunked` for up to 10 minutes without buffering or cutting the connection? If no, Contender B is off the table. How to check: deploy a test endpoint that streams chunks over 10 minutes; observe. Or ask Render support.

2. **Who are the clients of `POST /sessions`?** If only Fairy's own UI + internal testing, the API change in B is trivial to coordinate. If there are external integrations (agents calling agents?), the change needs a deprecation path. Check CLAUDE.md, README, and the e2e test suite for evidence of external consumers.

3. **What is Render's platform-level SIGTERM-to-SIGKILL window on the starter plan?** Both A and C want `--graceful-timeout 650`. If Render force-kills in 30 s regardless, that flag is aspirational. Determines whether deploy-loss is fixable in A without also doing C.

4. **Is there appetite for a second Render service?** Gates C. Operational + $7/mo + monitoring overhead. Worth it only if (a) we've measured deploy-loss rate as meaningful or (b) we're sure we want retry semantics regardless.

5. **Can the Sprites SDK's internal asyncio future actually be cancelled cleanly?** A's cancel-via-future depends on this. Small spike needed — instantiate a `Command`, inspect its internal state, try calling `.cancel()` on the `concurrent.futures.Future` returned by `run_coroutine_threadsafe`. If cancellation doesn't actually kill the WebSocket frame loop, A can't close Muddy Zone 8 cleanly and A's value drops.

## Process

1. **Land [`thoughts/plans/2026-04-19-threading-bug-fixes.md`](/thoughts/plans/2026-04-19-threading-bug-fixes.md) first.** Those fixes are unambiguously good regardless of which contender wins.
2. **Answer the five open questions above.** Spikes, not meetings — a day of probing gives us the data.
3. **Pick a contender.** Write its implementation plan (a separate document).
4. **Ship.**

## What each implementation plan would contain

Just to make the "we're deferring, not just punting" concrete — here's the rough shape each follow-up would take:

**If A wins:** `thoughts/plans/2026-04-19-threadpool-plus-gthread.md` covering the executor, `gthread` config, cancel-via-future spike, tests that actually exercise the state machine (since `Thread` is no longer mocked in the same way).

**If B wins:** `thoughts/plans/2026-04-19-streaming-post-refactor.md` covering the HTTP contract change, deletion of `run_session_background`, client migration plan, Render streaming-POST verification, CHANGELOG.

**If C wins:** `thoughts/plans/2026-04-19-procrastinate-worker.md` covering the Render service config, Procrastinate integration, migration, task function port, retry/DLQ semantics, deploy strategy.

## Related

- Research: [`thoughts/research/2026-04-19-threading-in-web-server.md`](/thoughts/research/2026-04-19-threading-in-web-server.md)
- Bug-fix baseline: [`thoughts/plans/2026-04-19-threading-bug-fixes.md`](/thoughts/plans/2026-04-19-threading-bug-fixes.md)
- Prior art on session execution model: [`thoughts/plans/2026-04-16-session-based-execution.md`](/thoughts/plans/2026-04-16-session-based-execution.md), [`thoughts/research/2026-04-18-sprites-script-setup.md`](/thoughts/research/2026-04-18-sprites-script-setup.md)
