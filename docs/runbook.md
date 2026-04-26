# Runbook

What to do when production is on fire. Scan the symptoms list first, jump to the matching section. If nothing matches, start at "General triage."

## Production topology

Three Render services in one project (`render.yaml`):

| Service                     | What it is                                            |
| --------------------------- | ----------------------------------------------------- |
| `agent-on-demand-api`       | Django ASGI (uvicorn, 3 workers). Accepts HTTP, creates DB rows, enqueues Procrastinate jobs. Never blocks on Sprites. |
| `agent-on-demand-worker`    | Procrastinate worker, `--concurrency 4`. Runs `provision_session`, `execute_turn`, `destroy_session`. All blocking Sprites calls happen here. |
| `agent-on-demand-db`        | Postgres 16, `standard-1gb`. Shared by both services + the Procrastinate broker. |

External dependencies:

- **Sprites** — runs the agent CLI inside a container per session. Outage = all new sessions fail at provision.
- **Model providers** (Anthropic, OpenAI, Google) — per-user keys stored encrypted in `UserRuntimeKey`. Outage of a provider = that runtime's sessions fail inside the agent process.
- **Honeycomb** — traces + logs. Two datasets: `aod-web`, `aod-worker` (driven by `OTEL_SERVICE_NAME`).
- **PostHog** — product events + exception capture. Session lifecycle events (`session.completed`, `session.failed`, `session.provision_failed`, `session.output_chunks_dropped`, `session.log_write_retry_exhausted`, `session.cmd_thread_leaked`) land here.

## Where to look

| Signal                         | Where |
| ------------------------------ | ----- |
| Alerts                         | Slack `#alerts` |
| External `/health` reachability | Checkly — scheduled `GET /health` from outside our network. `/health` exercises DB + field-encryption round-trip; a 503 here means Render auto-rollback should already be firing. See `views/health.py`. |
| Request rate, error rate, p95  | Honeycomb `aod-web` |
| Worker task durations, failures | Honeycomb `aod-worker` — look for `session.provision_task` and `session.execute_turn` spans |
| Session outcomes by runtime    | PostHog — event names above |
| Deploys, service health, logs, shell | Render dashboard |
| Queue depth                    | `SELECT status, queue_name, count(*) FROM procrastinate_jobs GROUP BY 1,2;` |

## Alerts to configure

Five triggers cover the regressions we most want to catch in the first 5 minutes after a deploy. Set these up in the listed tool with destination Slack `#alerts`. More than five and people start ignoring; fewer and we miss things.

**1. Web 5xx rate spike** — Honeycomb `aod-web`

```
COUNT WHERE response.status_code >= 500 GROUP BY 1m
```

Trigger: more than 5/min for 3 consecutive minutes. Catches the generic "deploy broke a backend handler" class of regression.

**2. `session.completed` volume drop** — PostHog

Insight: count of `session.completed` events, last 30 min vs. previous 30 min. Trigger: > 50% drop. This is the canary for "agents stopped working" — the most user-visible failure mode that doesn't surface as a 5xx.

**3. `session.provision_failed` spike** — PostHog

Insight: count of `session.provision_failed` events, last 5 min. Trigger: > 5 in 5 min (or > 10× baseline). Specifically catches Sprites outages, env-var decryption failures, and auth-to-Sprites bugs — none of which appear as web 5xxs because they happen in the worker.

**4. Worker `execute_turn` error rate** — Honeycomb `aod-worker`

```
COUNT WHERE error = true AND name = "session.execute_turn" GROUP BY 1m
```

Trigger: > 5 errors in 5 min. The worker side is invisible to the `aod-web` 5xx alert; this is its counterpart.

**5. `session.failed` rate** — PostHog

Insight: ratio of `session.failed` to (`session.completed` + `session.failed`), last 30 min. Trigger: failure ratio > 30% (typical baseline is < 5%). Stronger than #2 because it catches "sessions still start but most of them break" — a regression that volume metrics miss.

---

## General triage

1. Check **Render** — any service unhealthy or mid-deploy? If a bad deploy just went out, roll it back first; diagnose later.
2. Check **Honeycomb `aod-web`** — is the error rate elevated, or is traffic just gone? `/health` is excluded from traces, so "no spans" with a healthy service means no traffic, not no instrumentation.
3. Check **Honeycomb `aod-worker`** — are `session.execute_turn` spans still flowing? If the web service looks fine but sessions are stuck, the worker is the suspect.
4. Check **queue depth** (SQL above). A growing `todo` count with zero `doing` means no worker is pulling.

---

## Symptoms

### Web service returning 5xx / `/health` failing

**Diagnose.** Render → `agent-on-demand-api` → Logs. Look for tracebacks. Cross-check against recent deploys (Render → Deploys).

**Fix.**
- Recent deploy: **roll back** via Render → Deploys → Rollback. That is the default move during launch — debug after.
- Not a deploy: check DB connectivity (next section) and `DATABASE_URL` env var.
- If `/health` is 200 but real endpoints 500: likely app-level, check Honeycomb for the failing span, fix forward.

### Sessions stuck in `pending`

A session row exists, but nothing ever moves it to `running`. This means the Procrastinate worker isn't pulling the `provision_session` task.

**Diagnose.**
```sql
SELECT status, queue_name, COUNT(*) FROM procrastinate_jobs GROUP BY 1, 2;
```
If `todo` is non-zero and `doing` is zero, the worker is dead or not connected.

Render → `agent-on-demand-worker` → Logs. Look for crash traces or DB connection errors.

**Fix.**
- Restart the worker service in Render (Manual Deploy → Deploy latest commit, or Restart).
- If the worker is up but jobs aren't moving, check `DATABASE_URL` on the worker — both services must hit the same DB for Procrastinate to work.
- If a single bad job is blocking: find it (`SELECT id, task_name, args, attempts FROM procrastinate_jobs WHERE status = 'doing' ORDER BY id;`), then `UPDATE procrastinate_jobs SET status = 'failed' WHERE id = <id>;` and mark the session `failed` so the caller sees the outcome.

### Sessions stuck in `running`

The worker started the turn but never finished. Usually the worker was killed mid-execution (deploy, OOM, Render restart).

**Diagnose.** `SELECT id, runtime, updated_at FROM agent_sessions WHERE status = 'running' AND updated_at < NOW() - INTERVAL '30 minutes';`

**Fix.** The turn won't self-heal — task retry policy is **none** (see `session_service/tasks.py`). Options:
- Manually mark the affected sessions `failed`:
  ```sql
  UPDATE agent_sessions SET status = 'failed', updated_at = NOW() WHERE id IN (...);
  UPDATE session_turns SET status = 'failed', ended_at = NOW() WHERE session_id IN (...) AND status = 'running';
  ```
- The Sprite will time out server-side; no manual cleanup needed on the Sprites side.
- If this is happening regularly, check worker logs around the timestamp for OOM or restarts.

### All new sessions failing at `provision_failed`

Pattern: `session.provision_failed` PostHog events spiking, session status goes `pending → failed` with no `running` state in between.

**Diagnose.** Honeycomb `aod-worker`, filter on `name = "session.provision_task"`, group by `aod.failure_stage`. The stage (`no_sprites_key`, `create_sprite`, `setup_env`, ...) tells you where it broke.

**Fix by stage.**
- `no_sprites_key` — the user doesn't have a Sprites key configured. Not an incident; individual user issue.
- `create_sprite` — Sprites outage. Check Sprites status. Nothing to do service-side; wait.
- `setup_env` / later stages — likely a bad `Environment` config from the user (invalid packages, setup script). Check `Environment.setup_script` for the affected users.
- Spike across all users + all stages — something we shipped. Roll back.

### Auth broken / sudden 401 surge

**Diagnose.** Honeycomb `aod-web`, filter `http.status_code = 401`. Check `api_key.is_active` and `api_key.expires_at` for the affected user.

**Fix.**
- Single user: they rotated or their key expired. Ask them to generate a new one at `/ui/api-keys`.
- Everyone: something is wrong with `FIELD_ENCRYPTION_KEY` — that key encrypts `UserRuntimeKey.encrypted_key` and `SessionResource.encrypted_token`, but *not* `APIKey` (APIKeys are hashed, not encrypted). If all 401s: check `DJANGO_SECRET_KEY` didn't get rotated accidentally (sessions tie to it, not bearer auth, but a bad deploy could have other effects).
- **Never rotate `FIELD_ENCRYPTION_KEY` in production without a migration plan** — every stored user runtime key and repo token becomes unrecoverable.

### `session.output_chunks_dropped` firing

Session output is being lost because the in-process queue (maxsize=4096) filled faster than `bulk_create` could drain it. Data already written is fine; some chunks in the middle are missing.

**Diagnose.** PostHog → `session.output_chunks_dropped` → properties give session_id, dropped_count. Also check `session.log_write_retry_exhausted` — if that's also firing, the DB is the bottleneck.

**Fix.**
- Low volume: tell affected user that the session logs have gaps; re-run if needed.
- Sustained: Postgres is slow. Check `agent-on-demand-db` metrics in Render (CPU, connections, disk). Upgrading the DB plan is the fastest mitigation.

### Worker command thread leak (`session.cmd_thread_leaked`)

The SDK call didn't return within 5s of sentinel. The worker process is still alive but one concurrency slot is consumed by a zombie thread. With `--concurrency 4`, four of these brick the worker.

**Fix.** Restart `agent-on-demand-worker` in Render. File a bug with the session_id — this shouldn't happen and points at an SDK or Sprites issue.

### Postgres disk / connections

`standard-1gb` plan. If disk fills: logs are the biggest table — `AgentSessionLog`. Check size:
```sql
SELECT pg_size_pretty(pg_total_relation_size('agent_session_logs'));
```
No retention policy exists today. Options: upgrade the plan, or delete logs for old/terminated sessions. Coordinate before deleting — we haven't written a retention tool yet.

If connections exhausted: web uses 3 uvicorn workers, worker uses `--concurrency 4`, plus Procrastinate's own listener. All share one DB. Render's Postgres plan has a connection cap — check it before increasing concurrency anywhere.

### Checkly `/health` check failing

The scheduled Checkly run went red. Checkly hits `GET /health` from an external region — a failure means the web service is unreachable from the public internet.

**Diagnose.**
- If Render → `agent-on-demand-api` shows the service as healthy, the issue is between Checkly and Render: DNS, cert expiry on `aod.ravi.id`, or Render networking. Try `curl https://aod.ravi.id/health` yourself; if it works, suspect Checkly's region or a transient hiccup before escalating.
- If Render also shows the web service unhealthy, fall through to "Web service returning 5xx / `/health` failing" above.

**What Checkly does *not* catch.** It only proves the web process is answering. It won't detect:
- Worker stuck / sessions backed up (web `/health` stays green)
- Sprites outage (same)
- All sessions failing (same)

For those, watch Honeycomb `aod-worker` and PostHog session events.

### Bad deploy — rollback

Render → `agent-on-demand-api` → Deploys → find the last green deploy → Rollback. Repeat for `agent-on-demand-worker` if you deployed both. The services deploy independently; rolling back web but not worker (or vice versa) is fine as long as the DB migrations are compatible (they almost always are — we additive-migrate).

Do **not** rollback past a migration without checking `migrations/` — the worker and web must agree on schema.

### Auto-revert (failed deploys)

`.github/workflows/auto-revert.yml` polls Render every 5 min for failed deploys of `agent-on-demand-api`. When it finds one, it opens a `git revert` PR on the offending commit so `main` doesn't keep building on poison. **Render's auto-rollback handles the running image** — this workflow only handles the source-of-truth (`main`).

**What you'll see when it fires.** A new PR titled `Auto-revert: <short-sha> (<status>)` from `github-actions[bot]`, base `main`. The body has the deploy ID, commit SHA, and instructions. CI runs against it like any other PR.

**Decide and act.**
- **Merge** if the revert is correct (you don't have a hotfix in flight).
- **Close** if the failure was a Render flake or you've already pushed a forward-fix.
- **Don't ignore.** Until merged or closed, every subsequent failure on the same commit is silently deduped against this PR.

**Migration warning.** If the failed commit touched `src/agent_on_demand/migrations/`, the PR opens as a **draft** with the `needs-human-review` label. Code revert alone does **not** undo a migration. Either add a rollback migration to the PR before merging, or close the PR and resolve forward.

**Limitations.**
- Detection latency is up to 5 min (cron interval). Fine for `main`-cleanup; user-impact is already handled by Render auto-rollback in ~30s.
- Only the API service is monitored. Worker failures don't trigger auto-revert. (Worker deploys rarely fail in isolation since they share the same commit.)
- PRs created by `GITHUB_TOKEN` don't trigger downstream workflows. If CI doesn't appear on the auto-revert PR, push an empty commit (`git commit --allow-empty -m "trigger CI" && git push`) or re-run checks manually.
- Reverts of reverts are skipped (subject starts with `Revert `) so a botched revert can't loop.
- Dedup is via `gh pr list --search`, which routes through GitHub's search API and can lag by seconds to minutes. In rare cases, two consecutive cron ticks against the same still-failing commit may both miss the existing PR and open a duplicate. If you see two open auto-revert PRs for the same SHA, close the newer one — the script won't crash on the duplicate, just wastes a PR slot.

**Disabling.** Comment out the `schedule` trigger in `.github/workflows/auto-revert.yml`, or remove the `RENDER_SERVICE_ID_API` repo variable (the workflow `if:` short-circuits without it).

**Manual run.** Actions tab → "Auto-revert failed deploys" → "Run workflow" → toggle "Dry run" to preview without opening PRs.

**Setup (one-time).**
1. Repo Settings → Secrets → add `RENDER_API_KEY` (Render dashboard → Account Settings → API Keys).
2. Repo Settings → Variables → add `RENDER_SERVICE_ID_API` set to the `srv-...` ID of `agent-on-demand-api` (visible in the Render dashboard URL for the service).
3. (Optional) Repo Labels → create `needs-human-review` so draft PRs get tagged. The script tolerates absence.

---

## Scheduled / rare operations

### Rotate a user's API token

They do it themselves at `/ui/api-keys`. Old keys stay active until deactivated — they should deactivate the old one after swapping.

Admin override: Django admin (`/admin/fairy/apikey/`) can deactivate any key.

### Rotate `DJANGO_SECRET_KEY`

Django sessions for the UI break; API bearer auth is unaffected (bearer tokens are hashed against `APIKey.key_hash`, which doesn't use the secret key). Safe to rotate; users will get logged out of the web UI.

### Rotate `FIELD_ENCRYPTION_KEY`

**Don't, without a plan.** This key encrypts:
- `UserRuntimeKey.encrypted_key` (every user's Anthropic/OpenAI/Google/Sprites API key)
- `SessionResource.encrypted_token` (repo access tokens)

Rotating invalidates all of the above. A rotation plan needs a dual-key read path first. If the key is leaked, the correct move is coordinated: notify users, have them re-enter keys after we deploy the new one.

### Seed / invite a new user

Django admin at `/admin/`. Create a `User`, then create an `APIKey` for them — copy the raw key from the success message, it's only shown once.

Self-serve path: the `/ui/` onboarding flow creates a user + an initial API key in one go.

---

## What's *not* here

- **SLOs / error budgets** — intentionally deferred until we have real usage to measure against.
- **Status page** — deferred. If we're down during launch, post to the launch thread.
- **On-call rotation** — it's just us. Check Honeycomb + PostHog once a day during launch week.
