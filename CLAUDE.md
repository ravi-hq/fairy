# CLAUDE.md

Instructions for Claude Code when working in this repo.

## Project

Fairy is a Django REST API for running AI coding agents on Sprites. It manages
three resources: **agents**, **environments**, and **sessions**. See `README.md`
for the full API surface.

## Where things live

- `src/config/` — Django project config (`settings.py`, root `urls.py`)
- `src/fairy/` — The app
  - `models.py` — `Agent`, `Environment`, `Session`, version history
  - `views.py` — All HTTP endpoints
  - `urls.py` — Route table (single source of truth for the API surface)
  - `runtimes.py` — `AgentModel` enum and `MODEL_RUNTIME_MAP`
  - `sprites_exec.py` — Session execution on Sprites
  - `stream.py` — SSE streaming for session output
  - `auth.py`, `crypto.py` — Bearer-token auth and env-var encryption
- `tests/` — Unit + integration tests (Django test client)
- `tests/e2e/` — End-to-end tests against a running deployment

## Commands

```bash
make install       # uv sync --all-extras
make dev           # runserver 0.0.0.0:8777
make test          # unit + integration (tests/, excluding tests/e2e)
make test-e2e      # full e2e suite (needs FAIRY_API_TOKEN)
make test-e2e-fast # e2e without @slow tests
make test-all      # everything; e2e auto-skips without a token
make lint          # ruff check
make fmt           # ruff format + ruff check --fix
```

Dev server runs on `:8777`, **not** the Django default `:8000`. The `make
test-e2e*` targets default `FAIRY_API_URL` to `http://localhost:8777` to match;
export a different value to point at another deployment. If you invoke pytest
directly (without `make`), `tests/e2e/conftest.py` defaults to
`http://localhost:8000`.

## Conventions

- **Runtimes**: new models must be added to both `AgentModel` *and*
  `MODEL_RUNTIME_MAP` in `src/fairy/runtimes.py`.
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
  `POST /prompt` is only valid on a non-running, non-terminated session (multi-turn
  resumes the run); otherwise it returns `409`.

## Testing guidance

- Unit tests use `pytest-django` with a Django `Client` fixture from
  `tests/conftest.py`. They do **not** hit Sprites or any model runtime.
- E2E tests live in `tests/e2e/` and require a running Fairy deployment +
  a valid `FAIRY_API_TOKEN`. Without the token they auto-skip.
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
