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
    - `provisioning.py` — `provision_session`, per-stage helpers
    - `tasks.py` — `execute_turn` Procrastinate task (runs in worker process)
    - `turn.py` — `run_turn` (enqueues the task)
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
- **Environment `env_vars`** are returned in API responses (so clients can
  diff before updating). Storage is plaintext today; if you add new
  truly-secret fields, follow `crypto.py` for at-rest encryption and
  redact at the serializer layer.
- **Session states**: `pending → running → {completed, failed, terminated}`.
  `POST /prompt` is only valid on a `pending`/`completed` session;
  `running`, `failed`, and `terminated` all return `409`.

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

## Style

- Python 3.11+, ruff with `line-length = 100`.
- No new top-level documentation files unless asked.
- Don't add comments explaining *what* code does — names should carry that.
  Only add a comment when the *why* is non-obvious.
