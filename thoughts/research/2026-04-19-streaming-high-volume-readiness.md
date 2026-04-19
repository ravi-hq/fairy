---
date: 2026-04-19T11:10:00-07:00
researcher: Claude Code (team-research skill)
git_commit: fc7d41b0641dff82e404647b195be02c761ff4be
branch: main
repository: ravi-hq/agent-on-demand
topic: "Is our streaming implementation up to snuff for high-volume load?"
tags: [research, team-research, streaming, sse, scale, gunicorn, procrastinate, postgres]
status: complete
method: agent-team
team_size: 5
tracks: [sse-endpoint, producer-path, database, client-ux, thoughts-history]
last_updated: 2026-04-19
last_updated_by: Claude Code
---

# Research: Is our streaming implementation up to snuff for high-volume load?

**Date**: 2026-04-19
**Researcher**: Claude Code (team-research)
**Git Commit**: [`fc7d41b`](https://github.com/ravi-hq/agent-on-demand/commit/fc7d41b0641dff82e404647b195be02c761ff4be)
**Branch**: `main`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (5 specialist researchers)

## Research Question

Is our streaming implementation up to snuff? When we get into a high volume
situation, is it going to bite us?

## Summary

**Yes, it will bite — and the ceiling is lower than it looks.** The biggest
single problem is structural: the SSE endpoint pins one sync Gunicorn worker
per connected client for the entire session duration, and we only run three
workers. That's a hard **3 concurrent SSE clients** cap before *all* HTTP —
including `POST /sessions`, `/health`, and any non-streaming endpoint — is
blocked. This problem was identified and named in prior research
(`2026-04-19-threading-in-web-server.md`), the specific fix was chosen
(`--worker-class=gthread --threads=N`), but **the fix never shipped** —
`render.yaml` still uses sync workers.

Beyond that, three compounding failure modes show up at volume:

1. **Storage runway is short.** `AgentSessionLog` has no retention logic.
   At ~100 sessions/day × ~1 MB of logs each, the `basic-256mb` Postgres
   plan fills in ~2 days.
2. **The producer can stall the agent.** `output_q` is a bounded blocking
   queue; if the DB writer falls behind, the Sprite SDK's stdout thread
   blocks, which can time out the running agent.
3. **Procrastinate worker concurrency is defaulted to 1.** The recently
   shipped worker process runs turns one at a time unless configured
   otherwise; multi-tenant load will serialize.

The good news: indexes are correct, there are no obvious auth/isolation
bugs, and the DB tailing pattern itself is appropriate for our model (we
own the logs because Sprites can't replay them). The architecture is
sound; it's under-provisioned and under-configured for volume.

Below, **the top 6 risks** by severity, then full findings by track.

### Top 6 risks (ordered by blast radius)

| # | Risk | Severity | Fix effort | Status |
|---|------|----------|-----------|--------|
| 1 | **3-client SSE saturation ceiling** — 3 sync workers × 1 pin per stream ties up the whole web service at 3 concurrent streams | Critical | Low (config change) | Known, unfixed |
| 2 | **No log retention** — `AgentSessionLog` grows unbounded; 256 MB Postgres fills fast | High | Medium (cleanup job + policy) | Not discussed |
| 3 | **Producer queue stall can time out the agent** — full `output_q` blocks the SDK stdout thread | High | Medium (switch to `put(block=False)` + drop policy or larger queue) | Not discussed |
| 4 | **Procrastinate worker runs 1 turn at a time** — no `--concurrency` flag set | High | Low (config change) | Not discussed |
| 5 | **Slow-client DoS** — a client that stops reading holds a worker indefinitely; `--timeout 700` doesn't fire during `time.sleep(0.5)` between chunks | High | Medium (watchdog + idle kill) | Not discussed |
| 6 | **Worker crash leaves session `running` forever** — SSE stream never terminates, holding a worker | Medium | Medium (watchdog / heartbeat timeout) | Follow-up noted |

## Research Tracks

### Track 1: SSE endpoint & request lifecycle
**Researcher**: `sse-researcher`
**Scope**: `stream.py`, `views/sessions.py::stream_session`, `render.yaml`, `settings.py`

1. **3-worker hard ceiling (structural)** — `render.yaml:21` sets `--workers 3` sync. Each active SSE connection pins one worker for the life of the session (can be minutes to 10 min given `--timeout 700`). At 3 concurrent streams the whole web service is saturated. Severity: **high**. Evidence: `render.yaml:18-25` startCommand.
2. **Slow-client DoS via timer skew** — `views/sessions.py:317` wraps the generator; Gunicorn's `--timeout 700` watches between requests, but the 500 ms `time.sleep()` cycles in `stream.py:57` repeatedly refresh the liveness signal. A client that stops reading causes the write to TCP to block — the worker stays pinned. Severity: **high**. Evidence: `stream.py:57`.
3. **DB query rate per client** — 2 queries per 500 ms per client (log tail + `AgentSession.objects.get`), plus a JOIN to `session_turns` via `turn__turn_number` on every poll (`stream.py:24`). At N clients: 4N queries/sec, baseline, regardless of whether any new log rows exist. Severity: **medium**. Evidence: `stream.py:22,24,42`.
4. **Connection lifecycle during long streams** — `settings.py:73` sets `conn_max_age=600`. The SSE generator never calls `close_old_connections()` in-loop; connection cleanup waits for the request to end, which may never happen for a stuck stream. Severity: **medium**. Evidence: `settings.py:73` + `stream.py:20-57`.
5. **Heartbeat works through the proxy** — `X-Accel-Buffering: no` is set (`views/sessions.py:319`); 15 s heartbeat is frequent enough to keep Render's edge alive. But heartbeats are SSE comments (`": heartbeat\n\n"`) — invisible to `EventSource.onmessage`, not useful as a client-side liveness signal unless the client explicitly parses them. Severity: **low**. Evidence: `views/sessions.py:312-319`.
6. **Memory is bounded per loop iteration** — `[:100]` cap on the query means no unbounded accumulation. No memory concern. Severity: **low**. Evidence: `stream.py:22-25`.

### Track 2: Log producer path (worker → DB)
**Researcher**: `producer-researcher`
**Scope**: `session_service/tasks.py`, worker config

1. **Bounded blocking queue can stall the agent** — `output_q: queue.Queue(maxsize=4096)` in `tasks.py:325`. `TaggingQueueWriter.write` calls `queue.put(...)` with no timeout/`block=False`. If the DB consumer falls behind (e.g., bulk_create latency spike, transient DB slowness), the SDK's stdout thread blocks, which stalls `sprite.command().run()`. Under sustained high-volume output, this risks tripping the turn `timeout` and marking the session `failed`. Severity: **high**. Evidence: `tasks.py:84-87, 325`.
2. **Write cadence is tight** — `FLUSH_SIZE=20` plus a 1-second idle flush (`tasks.py:57, 370-373`). A noisy session can trigger 50+ `bulk_create` calls/sec; each is a network round-trip. Severity: **medium**. Evidence: `tasks.py:57, 376-385`.
3. **Arbitrary byte-fragment chunks** — `TaggingQueueWriter` doesn't line-buffer. The SDK may emit byte fragments (mid-line, mid-UTF8). Each fragment is one `AgentSessionLog` row. Storage amplification scales with fragment count, not byte count. Severity: **medium**. Evidence: `tasks.py:70-87`.
4. **Daemon thread isolation guarded by convention, not enforcement** — `tasks.py:335-336` has a `NOTE:` comment warning against DB writes inside the inner thread. The thread never touches the DB today, but future edits could silently break the `close_old_connections()` contract. Severity: **low**. Evidence: `tasks.py:335-336`.
5. **`bulk_create` has no try/except** — a single DB write failure crashes out of `_flush_buffer`, propagates up, loses up to `FLUSH_SIZE` chunks, and fails the turn. Explicit no-retry policy (module docstring). For a transient DB blip, that's a permanent turn failure. Severity: **high**. Evidence: `tasks.py:329-332`.
6. **`cmd_thread.join(timeout=5.0)` has no `is_alive()` follow-up** — if the command thread is still running after 5 s (e.g., blocked on a full queue), we proceed to the next turn. The daemon thread keeps writing into a queue nobody drains; chunks are lost; cleanup never happens. Severity: **medium**. Evidence: `tasks.py:388`.
7. **Procrastinate worker runs one task at a time** — `render.yaml:57` uses `uv run python manage.py procrastinate worker` with no `--concurrency` flag. Default is 1. Multi-tenant load will serialize turns in a single FIFO queue. Severity: **high**. Evidence: `render.yaml:56-57`.

### Track 3: Database shape & query cost
**Researcher**: `db-researcher`
**Scope**: `models/sessions.py`, migrations, `render.yaml`

1. **Indexes are correct** — `(session_id, id)` composite index in migration 0002 covers the hot SSE query fully (range scan, no heap fallback). `(turn_id, id)` from migration 0011 covers per-turn queries. Severity: **low** (good news). Evidence: `src/agent_on_demand/migrations/0002_agentsession_alter_userruntimekey_runtime_and_more.py`, `models/sessions.py:128`.
2. **No log retention — unbounded growth** — `AgentSessionLog` has zero purge logic. Only deletion path is CASCADE from `DELETE /sessions/{id}`. At ~100 sessions/day × ~1 MB logs each, the `basic-256mb` plan fills in ~2 days. Severity: **high**. Evidence: no management commands, cron jobs, or admin actions; confirmed by exhaustive grep.
3. **Per-row storage amplification ~3-4×** — ~70 bytes of fixed overhead per row (PK, FK, UUID, timestamp, heap tuple header) + 2 B-tree index entries. For 50-byte chunks that's roughly 3-4× raw byte storage. Severity: **medium**. Evidence: `models/sessions.py:112-134`.
4. **No lock contention** — append-only table, INSERT + SELECT don't conflict in Postgres. Autovacuum has little to do. Severity: **low**. Evidence: `tasks.py:331`, `stream.py:22`.
5. **AgentSession.status churn is negligible** — 2-4 writes per turn; `update_fields=` limits dirty footprint; single heap page served from buffer cache. Severity: **low**. Evidence: `tasks.py:359-362`, `stream.py:42`.
6. **Connection pool math is tight** — `conn_max_age=600` persistent connections per worker; 3 web workers + 1 worker process. `basic-256mb` Postgres allows ~25 max connections. Each SSE stream holds a connection for the full session duration. Adding workers to fix #1 directly pressures the connection limit. Severity: **medium**. Evidence: `settings.py:73`, `render.yaml:21`, `stream.py:20-57`.
7. **No archive/purge story** — no management commands, no scheduled tasks, no admin bulk-delete for logs. `DELETE /sessions/{id}` is the only user-facing deletion, and it only works on non-running sessions. Severity: **high**. Evidence: exhaustive grep for delete/purge/truncate/retention.

### Track 4: Client UX & reconnect semantics
**Researcher**: `ux-researcher`
**Scope**: `site/docs/api/streaming.md`, `docs/openapi.yaml`, `session_detail` UI, e2e tests

1. **`turn_start` event undocumented** — server emits it (`stream.py:31`), docs only list: `start`, `output`, `exit`, `error`, `terminated`. Doc-following clients silently drop it. Severity: **medium**.
2. **No reconnect cursor** — every reconnect replays from `id=0` (`stream.py:16`). No `Last-Event-ID`, no `?since=`, no SSE `id:` field. Docs literally tell clients "count events and skip duplicates" (`streaming.md:106`). O(N) replay cost per reconnect; bad at volume. Severity: **medium**.
3. **Deterministic 500 ms latency on terminal event** — `stream_session_from_db` checks session status *after* draining chunks (`stream.py:42`). If the final chunk writes between check and exit, the terminal event waits for the next poll. Not a lost-event bug, but a predictable 500 ms tail latency. Severity: **low**.
4. **Heartbeat is documented but not framed as a liveness signal** — `streaming.md:37-39` mentions heartbeats; doesn't tell clients to treat >15 s silence as a health warning. Severity: **low**.
5. **Browser `EventSource` can't authenticate** — `@require_api_key` wants a Bearer header; `EventSource` can't send custom headers. Clients must use `fetch` + `ReadableStream`. Not documented. The in-repo UI sidesteps this entirely by rendering logs server-side (`ui/views.py:179-190`, `session_detail.html`) — so there's no in-repo reference SSE client. Severity: **medium**.
6. **Tenant isolation is fine** — `user=request.user` filter in `views/sessions.py:304`; `@require_GET` + `@require_api_key`; no SSRF/CSRF surface. Severity: **low** (no issue).
7. **Failure paths are ungraceful** — DB drop mid-stream, deleted FK, or crashed worker all cause the stream to either hang (worker crash → session stuck `running` → poll forever) or abrupt-close without a terminal SSE event. No watchdog, no stale-session cleanup. Severity: **high**.

### Track 5: Prior context from `thoughts/`
**Researcher**: `history-researcher`
**Scope**: all `thoughts/` files relevant to streaming / threading / worker

**Decisions already made — don't re-litigate:**
- **DB tailing is the intentional streaming architecture** (`2026-04-16-session-based-execution.md`). Sprites can't replay server-side, so we own log storage. Don't propose pub/sub or WebSockets as a replacement.
- **500 ms poll interval, 15 s heartbeat, `FLUSH_SIZE=20` batching** — all deliberate tradeoffs.
- **`failed` is now terminal** (PR #48, `2026-04-19-threading-bug-fixes.md`). Don't propose making it resumable.
- **The inner daemon thread in the worker task stays** — explicitly deferred optimization per the procrastinate-worker plan. Don't flag its existence as a new finding.
- **ASGI was explicitly rejected** — "the real problems aren't caused by sync-ness" (`2026-04-19-threading-in-web-server.md`, `2026-04-19-threading-architecture-decision.md`).
- **Celery/RQ/Dramatiq were explicitly rejected** — Redis adds cost for no advantage once Postgres is provisioned.

**Known open items from prior work:**
- **SSE saturation fix (`--worker-class=gthread --threads=N`) was identified but never shipped.** Confirmed against current `render.yaml` — still `--workers 3` sync. **This is the single highest-leverage open item.**
- **Procrastinate concurrency tuning** was flagged as unknown.
- **Retry policy** for turn failures was deferred.
- **`/health/worker` endpoint** was proposed as a follow-up, not yet shipped.
- **Log retention policy** — no cleanup mechanism exists.
- **Muddy Zone 3** — `write_prompt` orphans turn rows; still open.

**Gaps prior work didn't cover:**
- No scale benchmarks or capacity targets. The "3 concurrent SSE clients" number is structural math, not measured.
- No analysis of DB load at scale with concurrent sessions writing to `AgentSessionLog`.
- No analysis of the cost of polling × multiple viewers per session.

## Cross-Track Discoveries

- **#1 (SSE saturation) compounds with #2 (producer stall) and #4 (worker concurrency=1).** A single noisy session can stall the producer, whose blocked queue can time out the turn. Meanwhile the web side can only serve 3 stream viewers at once. A handful of customers monitoring their own sessions exhausts the service before any real "volume" is reached.
- **Fixing #1 requires watching #6 (connection pool).** Switching to gthread workers with e.g. `--threads 16` + 3 workers = up to 48 concurrent SSE clients, each holding a DB connection. `basic-256mb` Postgres caps ~25 connections. The fix for saturation directly pressures the DB connection ceiling.
- **Reconnect cost (Track 4 #2) × DB write rate (Track 2 #2) = scan explosion.** Every reconnect replays the full session from id=0; a long session with a flaky client generates expensive full-range scans on every resume. Indexes cover it (Track 3 #1), but the work still has to happen.
- **No terminal event on worker crash (Track 4 #7) = permanent worker pin (Track 1 #2).** A crashed worker leaves a session stuck `running`; the SSE stream polls forever; the Gunicorn worker serving that stream never returns. This is a self-inflicted slow-burn outage mechanism.

## Code References

| File | Tracks | Key findings | Link |
|------|--------|--------------|------|
| `render.yaml:18-25` | 1, 2, 3 | 3 sync workers, no gthread, no Procrastinate `--concurrency` | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/render.yaml#L18-L25) |
| `render.yaml:56-57` | 2 | Procrastinate worker start command, no concurrency flag | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/render.yaml#L56-L57) |
| `src/agent_on_demand/stream.py:20-57` | 1, 3, 4 | Poll loop, query shape, termination condition, heartbeat | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/agent_on_demand/stream.py#L20-L57) |
| `src/agent_on_demand/views/sessions.py:296-320` | 1, 4 | SSE view, auth, headers | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/agent_on_demand/views/sessions.py#L296-L320) |
| `src/agent_on_demand/session_service/tasks.py:321-388` | 2 | `_execute_turn_body`, queue bound, flush cadence, thread join | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/agent_on_demand/session_service/tasks.py#L321-L388) |
| `src/agent_on_demand/session_service/tasks.py:70-87` | 2 | `TaggingQueueWriter.write` — blocking put, no backpressure | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/agent_on_demand/session_service/tasks.py#L70-L87) |
| `src/agent_on_demand/models/sessions.py:112-134` | 3 | `AgentSessionLog` schema + indexes | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/agent_on_demand/models/sessions.py#L112-L134) |
| `src/config/settings.py:73` | 1, 3 | `conn_max_age=600` | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/src/config/settings.py#L73) |
| `site/docs/api/streaming.md` | 4 | Public contract — missing `turn_start`, no reconnect cursor | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/fc7d41b/site/docs/api/streaming.md) |

## Architecture Insights

- **The pattern is correct; the provisioning isn't.** DB-tailed SSE is a good fit for Fairy's model (Sprites can't replay, and multiple viewers of the same session are supported naturally). The issues are deployment config (workers, concurrency, retention) not architectural.
- **The worst problems are all "we haven't configured this yet" problems**, not "we built it wrong." That's a positive signal — the fixes are mostly small and reversible.
- **Every producer/consumer boundary has a shock absorber except one.** `bulk_create` batches writes, `time.sleep(0.5)` paces reads, 100-row `LIMIT` caps memory — but `output_q.put()` is unconditionally blocking. That's the one place producer-side backpressure leaks back into the agent, which is where volume pain will first be felt.
- **The retry policy (none) is deliberate.** Don't introduce retries for turn execution; the design expects failures to be surfaced to the caller. But that raises the bar for `bulk_create` to not be the thing that fails the turn on a DB blip.

## Historical Context

- `thoughts/research/2026-04-19-threading-in-web-server.md` — prior team research that identified SSE saturation and the gthread fix. Fix did not ship.
- `thoughts/plans/2026-04-19-procrastinate-worker.md` — recently landed; moved execution out of the web process. Left SSE endpoint unchanged and left the inner daemon thread in place.
- `thoughts/plans/2026-04-19-threading-architecture-decision.md` — rejected ASGI and Redis-backed queues.
- `thoughts/research/2026-04-16-session-based-execution.md` — the original decision to own log storage because Sprites can't replay.

## Related Research

- `thoughts/research/2026-04-19-threading-in-web-server.md`
- `thoughts/research/2026-04-18-session-backend-abstraction.md`
- `thoughts/research/2026-04-18-sprites-script-setup.md`

## Open Questions

1. **What's our actual target volume?** Sessions/day, concurrent viewers per session, concurrent sessions running. No capacity target exists in any doc.
2. **Per-session log size distribution?** We assume ~1 MB average. Is that real? (Informs retention math.)
3. **Is `basic-256mb` Postgres the right plan going forward?** Any fix for #1 (SSE saturation) directly pressures the connection limit.
4. **Retention policy**: delete logs after N days? Keep metadata but purge `data` field? Move to object storage?
5. **Should the web process still serve SSE at all?** Long-lived connections on a request/response server will always have friction. Consider a dedicated stream process later.

## Recommended Next Steps (not part of this research, but implied)

In priority order:

1. **Ship `--worker-class=gthread --threads=N`** (config-only; already specified in prior research).
2. **Add Procrastinate `--concurrency=N` to the worker command.**
3. **Add a retention/cleanup job for `AgentSessionLog`** + bump Postgres plan.
4. **Swap `queue.put()` for `put(timeout=X)` with a drop-or-error policy** in `TaggingQueueWriter`.
5. **Wrap `bulk_create` in a try/except with bounded retry** (not turn-retry — row-retry).
6. **Add watchdog for stuck-`running` sessions** → terminal event + mark failed.
7. **Add reconnect cursor (`Last-Event-ID` support)** + document `turn_start`.
