---
date: 2026-04-20T09:35:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 1d4b8f1305701413b51ea9230821fc107e20b04c
branch: feat/posthog-exception-capture
repository: ravi-hq/agent-on-demand
topic: "Splitting SSE off the API service — rung 1 (separate Django service) and rung 2 (ASGI conversion)"
tags: [research, team-research, streaming, sse, asgi, render, deploy, scale]
status: complete
method: agent-team
team_size: 5
tracks: [deploy-topology, code-split, asgi-conversion, client-contract, capacity-math]
last_updated: 2026-04-20
last_updated_by: Claude Code
---

# Research: Rung 1 (split SSE service) and Rung 2 (ASGI conversion)

**Date**: 2026-04-20
**Researcher**: Claude Code (team-research)
**Git Commit**: [`1d4b8f1`](https://github.com/ravi-hq/agent-on-demand/commit/1d4b8f1305701413b51ea9230821fc107e20b04c)
**Branch**: `feat/posthog-exception-capture`
**Repository**: ravi-hq/agent-on-demand
**Method**: Agent team (5 specialist researchers)

## Research Question

What does it take to execute rung 1 and rung 2 of the SSE-scaling plan?

- **Rung 1**: Same Django codebase, add a second Render service dedicated to `/sessions/*/stream`. Isolate the thread-pin workload from the API service.
- **Rung 2**: Convert the stream service to ASGI (uvicorn) so SSE uses coroutines instead of pinned threads, lifting per-instance ceilings from tens to thousands of concurrent streams.

## Summary

**Both rungs are tractable with modest code changes.** Rung 1 is primarily a `render.yaml` diff plus a client-URL contract change — no runtime code changes. Rung 2 requires an async conversion of `stream.py`, `stream_session`, and `require_api_key`, a stripped middleware list (PostHog is not async-compatible), creation of `config/asgi.py`, and adding `uvicorn` to dependencies.

Critical findings:
- **Render does not support path-based routing.** Splitting the service means `stream_url` must become an absolute URL on a second hostname (`stream.aod.ravi.id`). Clients that construct their own stream path break; clients that use the server-returned `stream_url` keep working. The e2e client currently does the former and will need a small fix.
- **Django 5.1+ is already in use; async ORM is available out of the box.** `psycopg3` is also already installed with native async support. There are no dependency blockers for rung 2.
- **PostHog's `PosthogContextMiddleware` is sync-only.** Under ASGI Django adapts it via thread-pool shim, which negates most of the coroutine benefit for long-lived SSE. The stream service should ship without PostHog middleware — the stream view itself emits no PostHog events, so there's no analytics loss.
- **At rung 1 with 2 instances × 32 threads, DB connections are not the ceiling.** 64 stream connections + ~12 baseline = 76 against Postgres' 97 limit. A third instance at M=32 would exceed the ceiling and require a Postgres plan bump.
- **The e2e -n 4 crash may not be pure thread saturation.** Capacity-math flags that 4 concurrent streams on 24 thread slots is too much headroom to starve `/health` alone. Likely additional mechanism: session orchestration contention or Procrastinate worker concurrency. Worth verifying during rung 1 rollout.

### Reconciled disagreement: `FIELD_ENCRYPTION_KEY`

- `deploy-topology` initially listed it as needed.
- `code-split` noted the stream request path never touches encrypted fields.
- **Final resolution**: NOT needed. `crypto._get_fernet()` (`crypto.py:8`) is lazy — only called when `encrypt()`/`decrypt()` are invoked. The stream path touches zero encrypted fields (`APIKey.key_hash` is SHA256 hex; `AgentSession`, `AgentSessionLog`, `SessionTurn` have no encrypted fields). Omit `FIELD_ENCRYPTION_KEY` from the stream service's `render.yaml` env vars.

## Research Tracks

### Track 1: Deploy topology
**Researcher**: `deploy-topology-researcher`
**Scope**: `render.yaml`, `settings.py`, `observability.py`, `apps.py`, Render docs (web-searched)

1. **Minimal `render.yaml` diff** — Second `type: web` service mirroring the API service's `fromDatabase`/`fromService` pattern. `DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `HONEYCOMB_API_KEY`, `POSTHOG_API_KEY` pulled from `fromService: agent-on-demand-api`. `OTEL_SERVICE_NAME=aod-stream` separates the Honeycomb dataset. `DJANGO_ALLOWED_HOSTS` must include `stream.aod.ravi.id` and `agent-on-demand-stream.onrender.com`. Full YAML block in the implementation section below. Evidence: `render.yaml:67-95` is the precedent.
2. **No path-based routing on Render** — Each web service gets its own `.onrender.com` subdomain and its own custom-domain hostname. The split requires a separate DNS entry (`stream.aod.ravi.id`) and an absolute `stream_url` in API responses.
3. **Health check strategy unchanged** — `/health` is a no-DB JSON response (`views/health.py:6`). At `gthread` 2×16=32 threads, health will always find a free thread even under full SSE pinning. No reserved capacity needed. Keep `healthCheckPath: /health`.
4. **Migrations: API-only** — Only `agent-on-demand-api` runs `preDeployCommand: migrate` (`render.yaml:17`). Do NOT add it to the stream service — concurrent migrations risk races. Schema-skew window is brief and low-risk because stream reads only.
5. **Env var classification** — All `os.environ.get` calls in `settings.py`, `observability.py`, `apps.py` were walked. `SPRITES_BASE_URL`, `SPRITE_NAME_PREFIX`, `DEFAULT_TIMEOUT` (all in `settings.py:107-109`) are NOT needed by stream service. Everything else is.

### Track 2: Code split surface
**Researcher**: `code-split-researcher`
**Scope**: `urls.py`, `views/sessions.py::stream_session`, `stream.py`, `auth.py`, `models/`, `apps.py`, `settings.py`

1. **Minimal module dependency set** — Stream path needs: `stream.py`, `auth.py`, `models/sessions.py` (AgentSession, AgentSessionLog, SessionTurn), `models/auth.py` (APIKey), `crypto.py` (transitive, decrypt only). Does NOT functionally need: `session_service/`, `runtimes.py`, `views/agents.py`, `views/environments.py`, `models/agents.py`, `models/environments.py`. Caveat: `apps.py:ready()` imports `signals.py`, which imports `session_service` — so the stream process loads session_service into memory at startup even though it's never invoked. Only eliminable if you fork the app config.
2. **Thinner `ROOT_URLCONF` is feasible but not needed** — A stream-only URLconf with just `/health` + `/sessions/<uuid>/stream` works, but since Render's load balancer already scopes traffic to the stream hostname, an unused route in memory costs nothing at request-time. Don't fork unless debugging shows the extra imports matter.
3. **Middleware — droppable for stream service** — Required: `CorsMiddleware` (for the cross-origin hostname), `CommonMiddleware`. Droppable: `WhiteNoiseMiddleware`, `SessionMiddleware`, `CsrfViewMiddleware`, `AuthenticationMiddleware` (auth happens in the decorator), `MessageMiddleware`, `PosthogContextMiddleware`. The minimal stack is `[CorsMiddleware, CommonMiddleware]`. Evidence: `stream_session` at `views/sessions.py:298-330` uses Bearer token auth, not cookies, and makes no PostHog calls.
4. **AgentSession read path is tight** — `stream_session` at `views/sessions.py:304` does `AgentSession.objects.get(pk=session_id, user=request.user)`. `stream_session_from_db` at `stream.py:54` does the same (without user filter — trusted caller). The `.values("id","stream","data","turn_id","turn__turn_number")` at `stream.py:32` JOINs only `session_turns`. Three tables touched in total: `agent_sessions`, `agent_session_logs`, `session_turns`.
5. **No shared state concerns** — `apps.py:ready()` (`apps.py:16-52`) does OTel init, PostHog init (with no-op if key absent), and a `connection_created` signal handler for SQLite pragmas (harmless on Postgres). No module-level caches or singletons.

### Track 3: ASGI conversion path
**Researcher**: `asgi-researcher`
**Scope**: `views/sessions.py`, `stream.py`, `auth.py`, `settings.py`, `pyproject.toml`, `apps.py`

1. **Django 5.1+ — no version blocker** (`pyproject.toml:18`). `aget()`, `aiterator()`, async `StreamingHttpResponse` (Django 4.2+), all available.
2. **`stream.py` async diff** — Convert `stream_session_from_db` to `async def` returning `AsyncGenerator[str, None]`. Replace `AgentSessionLog.objects.filter(...).values(...)[:100]` with `async for chunk in (...).aiterator()`. Replace `AgentSession.objects.get(...)` with `await AgentSession.objects.aget(...)`. Replace `time.sleep(0.5)` with `await asyncio.sleep(0.5)`. The `event_generator()` inner function in `views/sessions.py:314` must also become `async def` using `async for event in stream_session_from_db(...)`.
3. **`close_old_connections` under ASGI** — Django's `request_started`/`request_finished` signals fire under ASGI; `close_old_connections` is auto-called. `psycopg[binary]>=3.1` is already installed (native async support). No manual `sync_to_async` wrappers needed when using `.aget()` / `.aiterator()` natively.
4. **PostHog middleware NOT async-compatible** — `PosthogContextMiddleware` (`settings.py:39`) is sync. Under ASGI Django wraps it via thread-pool adapter, negating most of the benefit. Drop it from the stream-service middleware stack. The `posthog.capture()` calls elsewhere use posthog's own async background queue and are fine to keep — they're just not invoked on the stream path.
5. **OTel works under ASGI unchanged** — `DjangoInstrumentor` and `PsycopgInstrumentor` both support ASGI (opentelemetry-instrumentation-django ≥0.35, already satisfied). `observability.py` needs no changes.
6. **Use `uvicorn`** — Not daphne (requires Channels), not hypercorn (less maintained). Start command: `uv run uvicorn config.asgi:application --host 0.0.0.0 --port $PORT --workers 1`. `config/asgi.py` must be created (currently only `config/wsgi.py` exists). `uvicorn>=0.30` must be added to `pyproject.toml`.
7. **`@require_api_key` must be async** (`auth.py:9-43`). A sync decorator around an async view works via Django's sync-to-async adapter but wastes a thread on every stream request. Convert the decorator to use `APIKey.objects.select_related("user").aget(...)`. Either convert the single decorator (affects 6 views, all minimal) or ship a parallel `async_require_api_key` specific to the stream service.

**Required file changes for rung 2:**
- Create `src/config/asgi.py`
- Add `uvicorn>=0.30` to `pyproject.toml`
- `src/agent_on_demand/stream.py` → async generator
- `src/agent_on_demand/views/sessions.py:298-330` → async `stream_session` + `event_generator`
- `src/agent_on_demand/auth.py` → async `require_api_key` (or parallel async variant)
- `src/config/settings.py` → stripped MIDDLEWARE for stream (likely via `config.settings_stream`)

### Track 4: Client contract impact
**Researcher**: `client-contract-researcher`
**Scope**: `views/sessions.py`, `ui/`, `site/docs/api/streaming.md`, `docs/openapi.yaml`, `tests/e2e/conftest.py`

1. **`stream_url` is always a relative path** — `views/sessions.py:275` (create) and `views/sessions.py:430` (prompt) return `f"/sessions/{session.id}/stream"`. Clients that prepend an API base URL to this value will break under split-hostname.
2. **Smallest client change: server returns absolute URL** — The server can emit `"stream_url": "https://stream.aod.ravi.id/sessions/{id}/stream"` with zero protocol break. Clients that used `stream_url` verbatim keep working; those that built their own URL break.
3. **Docs assume same-origin** — `docs/openapi.yaml:10-12` has a single `servers` block; `site/docs/api/streaming.md` uses `$BASE/sessions/...`. Both need updating for a split topology.
4. **CORS — no blocker** — `settings.py:63` sets `CORS_ALLOW_ALL_ORIGINS = True`. All clients use Bearer tokens, not cookies. Moving the stream to a different origin requires no server-side CORS changes.
5. **In-repo UI does NOT consume SSE** — `session_detail.html:36` uses server-side template iteration. Confirmed the prior research claim.
6. **E2E client builds stream URL itself** — `tests/e2e/conftest.py:123-125` has `APIClient.stream_session_raw` call `self._url(f"/sessions/{sid}/stream")` against `AOD_API_URL`. It does NOT use the `stream_url` field from session responses. Either (a) fix the client to read `stream_url` from the create/prompt response, or (b) add a new `AOD_STREAM_URL` env var. Option (a) is cleaner and tests the real contract.

### Track 5: Capacity math and DB impact
**Researcher**: `capacity-math-researcher`
**Scope**: `render.yaml`, `settings.py`, `stream.py`, `thoughts/research/2026-04-19-streaming-high-volume-readiness.md`

1. **Postgres `standard-1gb` = 97 connection limit** (shared across all services).
2. **Current simultaneous connection potential ≈ 28** — 3 workers × 8 gthread = 24 web + 4 Procrastinate = 28. Against 97, not near ceiling. Today's bottleneck is Gunicorn thread count, not DB connections.
3. **Rung 1 ceiling at N=2 × M=32**: **64 concurrent streams max**, constrained by thread count, not DB (64 stream + 8 API baseline + 4 worker = 76 < 97). Each stream holds a connection for full session duration under rung 1. At N=3 × M=32, you'd exceed 97 connections and need a Postgres plan bump.
4. **Rung 2 ceiling ≈ order of magnitude over rung 1** — Django's async ORM (`aget`, `aiterator`) does NOT use native async psycopg3. It dispatches each query via `sync_to_async` to a thread-pool executor, borrowing a thread + DB connection for ~5ms per call, then releasing both. Across `await asyncio.sleep(0.5)`, no thread or connection is held. At 2 queries per 500ms = ~2% duty cycle per stream. So the 85 available Postgres connections can serve roughly `85 / 0.02 = ~4,250` concurrent streams in principle; in practice burstiness and variance make the true ceiling lower, but still an order of magnitude above rung 1's 85-stream hard cap. **New limiting factor at scale**: the `sync_to_async` thread-pool executor itself — at ~200 concurrent streams × 4 queries/sec = ~800 dispatches/sec, executor saturation shows up before Postgres does.
5. **Monitoring gaps** — Stream service with its own `OTEL_SERVICE_NAME=aod-stream` will have a separate Honeycomb dataset. Existing `aod-web` dashboards won't cover stream latency/errors. PostHog needs `POSTHOG_API_KEY` (via `fromService`) to track stream events — but if the stream service drops `PosthogContextMiddleware`, it won't auto-track anyway. Render's built-in per-service metrics are siloed — no cross-service dashboard.
6. **E2E `-n 4` crash diagnosis — not pure DB or thread ceiling** — 4 concurrent streams on 24 thread slots ≠ saturation. Capacity math suggests the failure mechanism is either (a) session orchestration races in the state machine, (b) Procrastinate worker concurrency pressure (4 concurrent provision + 4 concurrent destroy tasks competing), or (c) a specific path that opens more threads than 1-per-stream (e.g., setup/teardown calls overlapping with streams). Worth instrumenting before assuming the split alone fixes it.

## Cross-Track Discoveries

- **Track 1 ↔ Track 4**: Render's lack of path-based routing *forces* the client contract change — you can't hide the split behind a path on the same hostname. This elevates task 4's work from "maybe needed" to "required."
- **Track 2 ↔ Track 3**: Code-split's "drop PostHog middleware" and ASGI's "PostHog not async-compatible" are the same answer for different reasons — converging on the same minimal middleware stack.
- **Track 3 ↔ Track 5**: Async ORM's short-lived connection pattern is precisely what lifts rung 2's ceiling. Rung 2 at the same Postgres plan handles 10× more concurrent streams than rung 1.
- **Track 5 ↔ Track 1**: The N=3×M=32 scenario would push past Postgres' 97-connection limit — factor this into instance sizing. For rung 1, stay at N=2 × M=32 unless Postgres is upgraded.
- **Track 4 ↔ Track 5**: The e2e client-URL fix is a prerequisite for rung 1 testing, AND capacity-math's e2e-crash diagnosis suggests instrumenting the e2e runner itself to confirm the failure mode before shipping the split.

## Implementation outline

### Rung 1 — split sync service (1-2 days)

1. Add `agent-on-demand-stream` web service to `render.yaml`. **Recommended initial sizing: 1 instance × `--workers 2 --threads 16` = 32 DB connections.** That keeps you well under the 97-connection Postgres ceiling with current baseline (~28 connections from API + worker). Max safe: 2 instances × 32 threads = 64 stream connections (~92 total, just under 97). Beyond that, upgrade Postgres or add PgBouncer. Omit `FIELD_ENCRYPTION_KEY` from the env vars — stream path doesn't need it.
2. Create `stream.aod.ravi.id` DNS → `agent-on-demand-stream.onrender.com`.
3. Return absolute `stream_url` from `views/sessions.py:275` and `:430`. Source the hostname from a new setting `STREAM_BASE_URL` (env var), defaulting to `""` so local dev stays relative.
4. Update `tests/e2e/conftest.py:123-125` to read `stream_url` from the session response instead of constructing it.
5. Update `docs/openapi.yaml` to add a second `servers` entry for the stream host, and `site/docs/api/streaming.md` to describe the split.
6. Deploy. Run `make test-e2e E2E_WORKERS=4` and confirm no 502s.

**Startup cost caveat**: Even with only `/health` + `/sessions/*/stream` exposed, the stream service boots the full app: `views/sessions.py:16-26` imports `session_service`, `runtimes`, `Agent`, `Environment`, `UserRuntimeKey`; `apps.py:ready()` imports `signals.py:4` which imports all of `session_service`. Memory-only cost, no correctness issue. If stream-service RAM becomes a concern, extract `stream_session` into `views/stream.py` and write a custom `AppConfig` that skips the signals import — but do that after measuring, not preemptively.

### Rung 2 — ASGI on stream service only (2-3 days)

1. Add `uvicorn>=0.30` to `pyproject.toml`.
2. Create `src/config/asgi.py` (6 lines).
3. Create `src/config/settings_stream.py` importing from `settings.py` but overriding `MIDDLEWARE = [CorsMiddleware, CommonMiddleware]`.
4. Convert `auth.py::require_api_key` to async (or add `async_require_api_key` parallel).
5. Convert `stream.py::stream_session_from_db` to `async def` using `aiterator()` + `aget()` + `asyncio.sleep`.
6. Convert `views/sessions.py::stream_session` + `event_generator` to async.
7. Change the stream service's `startCommand` in `render.yaml`: `uv run uvicorn config.asgi:application --host 0.0.0.0 --port $PORT --workers 1 --env-file /dev/null` plus `DJANGO_SETTINGS_MODULE=config.settings_stream`.
8. Deploy and load-test: rung 2 should support 100+ concurrent streams on a single instance.

## Code References

| File | Tracks | Key findings | Link |
|------|--------|--------------|------|
| `render.yaml:11-50` | 1, 5 | Existing API service; pattern to mirror | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/render.yaml#L11-L50) |
| `render.yaml:67-95` | 1 | `fromService` env var inheritance pattern | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/render.yaml#L67-L95) |
| `src/agent_on_demand/views/sessions.py:275` | 4 | `stream_url` returned as relative path (create) | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/views/sessions.py#L275) |
| `src/agent_on_demand/views/sessions.py:430` | 4 | `stream_url` returned as relative path (prompt) | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/views/sessions.py#L430) |
| `src/agent_on_demand/views/sessions.py:298-330` | 2, 3 | `stream_session` view + `event_generator` (both need async conversion) | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/views/sessions.py#L298-L330) |
| `src/agent_on_demand/stream.py:14-77` | 2, 3 | Generator with `time.sleep(0.5)` — async target | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/stream.py#L14-L77) |
| `src/agent_on_demand/auth.py:9-43` | 2, 3 | `require_api_key` decorator — sync, needs async variant | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/auth.py#L9-L43) |
| `src/config/settings.py:39` | 2, 3 | `PosthogContextMiddleware` — drop for stream service | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/config/settings.py#L39) |
| `src/config/settings.py:63` | 1, 4 | `CORS_ALLOW_ALL_ORIGINS = True` — no CORS blocker | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/config/settings.py#L63) |
| `src/config/settings.py:72-74` | 5 | `conn_max_age=600` — per-thread DB connection | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/config/settings.py#L72-L74) |
| `src/agent_on_demand/apps.py:16-52` | 2 | `ready()` hook; signals import session_service at startup | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/src/agent_on_demand/apps.py#L16-L52) |
| `tests/e2e/conftest.py:123-125` | 4 | `APIClient.stream_session_raw` builds URL itself | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/tests/e2e/conftest.py#L123-L125) |
| `docs/openapi.yaml:10-12` | 4 | Single `servers` block assumes same-origin | [permalink](https://github.com/ravi-hq/agent-on-demand/blob/1d4b8f1/docs/openapi.yaml#L10-L12) |

## Architecture Insights

- **The structural fix is progressive.** Rung 1 buys isolation (a bad SSE doesn't take down `/sessions` creation) without changing any runtime code. Rung 2 buys capacity (thousands of streams per instance) by changing a small, bounded set of files. You don't have to commit to rung 2 when you do rung 1.
- **Render's no-path-routing is what forces the client contract change.** On a platform with path-based routing (e.g., Fly, Cloudflare), the split could be invisible to clients. Since we're on Render, `stream_url` becoming absolute is inescapable.
- **Rung 2 gets async ORM almost for free, but via `sync_to_async` under the hood** — Django 5.1 + psycopg3 are both already installed. Django's async ORM methods are a thread-pool shim, not native async connections. That's fine for our workload — the 2% duty cycle per stream means a small thread pool serves many coroutines. It just means async ORM doesn't magically remove the connection-per-query relationship; it amortizes it across time.
- **PostHog's sync middleware is the one structural incompatibility** with async Django on this path. Dropping it from the stream service aligns with both code-split minimization and ASGI performance — the same answer for two different reasons.

## Historical Context

- `thoughts/research/2026-04-19-streaming-high-volume-readiness.md` — prior team research that identified the SSE saturation problem and top-6 risks. This plan is the structural follow-up it recommends.
- `thoughts/research/2026-04-19-threading-in-web-server.md` — prior research that rejected ASGI for the *whole* service. That rejection's reasoning ("the real problems aren't caused by sync-ness") does not apply to a dedicated SSE service where sync-ness is exactly the problem.
- `thoughts/research/2026-04-19-threading-architecture-decision.md` — decision record rejecting Celery/RQ and full-ASGI.

## Open Questions

1. **What is the true e2e `-n 4` crash mechanism?** Capacity-math's analysis suggests 4 concurrent streams on 24 thread slots shouldn't saturate `/health`. Before assuming rung 1 alone fixes it, instrument `/health` latency + thread-pool occupancy + Procrastinate queue depth during an `-n 4` run. If the crash is session orchestration or worker-concurrency contention, the split helps but doesn't fully resolve it.
2. **Do we bump Postgres to `standard-2gb` proactively?** At rung 1 (N=2 × M=32) we have ~5 connections of headroom — tight. At rung 1 + N=3 or any further scale-out, we exceed 97 connections. Upgrading now future-proofs both rungs. Alternative: drop PgBouncer in transaction-pooling mode in front of Postgres — removes the raw-connection ceiling for both rungs at the cost of one more deploy artifact.
3. **Fork app config or single config?** Code-split's finding #1 notes that `apps.py:ready()` imports `signals.py` which imports `session_service` even in the stream service. Cost is memory, not correctness. Worth profiling stream-service RAM at idle before deciding.
4. **Should `send_prompt`'s `stream_url` also become absolute?** Yes for consistency. Include in the track-4 fix.
5. **Rate-limit the SSE endpoint at the stream service?** Not in scope for rung 1/2, but relevant once the service can handle thousands of concurrent streams.

## Related Research

- `thoughts/research/2026-04-19-streaming-high-volume-readiness.md`
- `thoughts/research/2026-04-19-threading-in-web-server.md`
- `thoughts/research/2026-04-19-threading-architecture-decision.md`
- `thoughts/research/2026-04-18-sprites-script-setup.md`
