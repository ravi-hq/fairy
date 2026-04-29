# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Project

Agent on Demand is a Django REST API for running AI coding agents on Sprites.
It manages three resources: **agents**, **environments**, and **sessions**. See
`README.md` for the full API surface.

## Where things live

- `src/config/` — Django project config (`settings.py`, root `urls.py`)
- `src/agent_on_demand/` — The app (Django app_label is `fairy` for DB compat)
  - `models/` — `Agent`, `Environment`, `Session`, version history
  - `views/` — All HTTP endpoints (per-resource routers)
  - `urls.py` — Route table (single source of truth for the API surface)
  - `runtimes/` — per-runtime `Runtime` classes (`claude.py`, `codex.py`, `gemini.py`, `opencode.py`) and the `RUNTIMES` registry
  - `models_catalog.py` — `MODELS` dict keyed by canonical `provider/model_id` strings
  - `session_service/` — Sprites orchestration
    - `provisioning/` — `provision_session`, per-stage helpers
    - `tasks.py` — `execute_turn` Procrastinate task (runs in worker process)
    - `turn/` — `run_turn` (enqueues the task), `build_turn_argv`, `compute_final_status`
  - `stream.py` — SSE replay endpoint (tails `AgentSessionLog`)
  - `auth.py`, `crypto.py` — Bearer-token auth and env-var encryption
- `tests/` — Unit + integration tests (Django test client)
- `tests/e2e/` — End-to-end tests against a running deployment

## Architecture

**Two-process deploy**: the `web` service (Django/Gunicorn) accepts HTTP, creates
DB rows, and enqueues Procrastinate tasks. The `worker` service (Procrastinate
worker) runs the blocking `sprite.command().run()` and writes logs to the DB.
No threads are spawned from the web process. Both services share one Postgres.
See `render.yaml` for deploy config.

## Commands

```bash
make install       # uv sync --all-extras
make dev           # runserver 0.0.0.0:8777 (web)
make worker        # procrastinate worker (requires Postgres)
make test          # unit + integration (tests/, excluding tests/e2e)
make test-e2e      # full e2e suite (needs AOD_API_TOKEN)
make test-e2e-fast # e2e without @slow tests
make test-all      # everything; e2e auto-skips without a token
make lint          # ruff check
make fmt           # ruff format + ruff check --fix
```

Dev server runs on `:8777`, **not** the Django default `:8000`. The `make
test-e2e*` targets default `AOD_API_URL` to `http://localhost:8777` to match;
export a different value to point at another deployment. If you invoke pytest
directly (without `make`), `tests/e2e/conftest.py` defaults to
`http://localhost:8000`.

## Conventions

- **Runtimes**: new models go in `src/agent_on_demand/models_catalog.py`; ensure
  the `provider` is in the target `Runtime`'s `providers` set. New runtimes are
  one file per class under `src/agent_on_demand/runtimes/` and must be added to
  the `RUNTIMES` dict in `runtimes/__init__.py`.
- **Versioning**: `agents` and `environments` use optimistic concurrency —
  updates require the current `version` and return a new row in
  `{agent,environment}_versions`. Tests to reference: `test_agents.py::TestAgentVersioning`,
  `test_environments.py::test_environment_versions`.
- **Metadata merge semantics**: `PUT` with `metadata={"k": ""}` deletes `k`;
  other keys in the payload are merged onto existing metadata. See
  `test_agents.py::test_metadata_merge_semantics`.
- **Archive is idempotent-error**: archiving an already-archived row returns
  `409`, not `200`. Same for terminating an already-terminated session.
- **Environment `env_vars`** are encrypted at rest and never returned in API
  responses. If you add new secret fields, follow the same pattern in
  `crypto.py`.
- **Session states**: `pending → running → {completed, failed, terminated}`.
  `POST /prompt` is only valid on a `pending`/`completed` session;
  `running`, `failed`, and `terminated` all return `409`. `POST
  /interrupt` is the inverse: only valid on `pending`/`running`. An
  interrupt brings the session back to `completed` (Sprite stays alive)
  and the affected turn finalizes with `status="interrupted"`.

## Testing guidance

- Unit tests use `pytest-django` with a Django `Client` fixture from
  `tests/conftest.py`. They do **not** hit Sprites or any model runtime.
- E2E tests live in `tests/e2e/` and require a running deployment +
  a valid `AOD_API_TOKEN`. Without the token they auto-skip.
- E2E `@pytest.mark.slow` tests spawn real agent sessions and cost money.
  Prefer `make test-e2e-fast` during iteration; run the full suite before
  landing changes that touch session execution, environment setup, or
  agent system prompts.
- E2E fixtures (`create_agent`, `create_environment`, `create_session` in
  `tests/e2e/conftest.py`) auto-clean up created rows after each test.

### Scoped e2e on PRs

The `e2e-scoped` CI job runs only the e2e tests that map to source files
changed on the current branch (via `scripts/scope_e2e.py`). The mapping
is in that script's `RULES` table and is pinned by `tests/test_scope_e2e.py`
— don't change either without updating the other.

To preview locally what e2e would run for your branch:

```bash
make scope-e2e          # prints the test files + runtimes
make test-e2e-scoped    # actually runs them (needs AOD_API_TOKEN)
```

**The CI job is currently a no-op preview** — it computes scope and prints
what *would* run, but actual pytest is gated on `AOD_API_TOKEN` and
`AOD_API_URL` repository secrets. To activate the gate, add both secrets
under Repository Settings → Secrets and variables → Actions.

## Danger zones

Files and patterns where mistakes have outsized blast radius. Treat any
non-trivial change to the files below as needing human review even if a
test passes — automated coverage cannot catch every class of bug here.

**Files: any change here requires explicit human review before landing.**

- `src/agent_on_demand/auth.py` — bearer-token check. A weakened comparison
  or short-circuited check is a full breach. Mutation-tested.
- `src/agent_on_demand/crypto.py` — Fernet wrapper for `env_vars` and other
  secrets at rest. A wrong key derivation silently corrupts every encrypted
  field. Mutation-tested.
- `src/agent_on_demand/versioning.py` — optimistic-concurrency mismatch
  response. The exact `detail` string is part of the API contract; SDKs
  parse it. Mutation-tested.
- `src/agent_on_demand/session_state.py` — session state-machine
  predicates. Wrong rejection = duplicate agent runs (\$\$\$) or stuck
  sessions. Mutation-tested.
- `src/agent_on_demand/migrations/*.py` — DB migrations are forward-only in
  prod. New migrations are linted in CI (`make check-migrations`); see
  `MIGRATION_LINTER_OPTIONS` in `src/config/settings.py`.

**Forbidden patterns (do not do these without explicit human approval):**

- Removing or weakening a `version` check in a write path. Optimistic
  concurrency is the only thing preventing silent data loss under
  concurrent updates.
- Removing the `select_for_update()` lock around session state changes
  (`terminate_session`, `delete_session`, `send_prompt`'s post-lock check).
  These prevent races that orphan Sprites or double-spend agent runs.
- Changing the response shape of any documented endpoint without
  also updating `docs/openapi.yaml` and `docs/request_schemas.json`. CI
  enforces request-schema snapshots; response shapes are pinned by tests.
- Skipping CI hooks (`--no-verify`, `--no-gpg-sign`) or disabling a
  required check. If a check fails, fix the underlying issue.
- Adding migrations that drop columns / tables, add NOT NULL on populated
  tables, or change column types without a backfill plan. The migration
  linter catches the mechanical patterns; the deploy-time impact still
  needs review.

**When the mutation-test gate fires unexpectedly,** read the survivor list
in the CI output — `scripts/check_mutmut.py` prints the exact `mutmut
show <name>` command. Don't add the survivor to the equivalent allowlist
unless you can prove (with the diff) that no input distinguishes the
mutant from the original.

**Coverage floor is a ratchet.** `make coverage` enforces
`[tool.coverage.report].fail_under` in `pyproject.toml`. PRs that add
tests should raise the floor toward the new total; PRs that remove tests
must justify lowering it. Treat the floor like a high-water mark — never
move it down to "make CI green."

**Ruff `select` is a ratchet.** `[tool.ruff.lint].select` in
`pyproject.toml` lists rule families that were zero-violation at the
time they were added. Each one prevents a class of bug from regressing
silently. To add a new family, run `ruff check --select=<rule>` until
clean, then merge it in. Don't drop a family from `select` to "make CI
green" — fix the violation or `# noqa` the specific line with a
justification.

## Production observability

`/health` exercises a real DB query plus a field-encryption round-trip — a
broken deploy that can't reach the DB or has a misconfigured
`FIELD_ENCRYPTION_KEY` returns 503, and Render auto-rollback fires. See
`src/agent_on_demand/views/health.py` and `tests/test_health.py`.

For post-deploy alerting (5xx spikes, session-completion drops, worker
error rates), see the **Alerts** section in `docs/runbook.md`. Five
Honeycomb + PostHog triggers cover the most common regression shapes;
4/5 are wired (the `session.completed` volume-drop trigger is still
pending).

## Style

- Python 3.11+, ruff with `line-length = 100`.
- No new top-level documentation files unless asked.
- Don't add comments explaining *what* code does — names should carry that.
  Only add a comment when the *why* is non-obvious.
