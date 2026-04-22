# Agent on Demand

API for running AI coding agents on Sprites.

## Documentation

Full API and operator documentation: **https://ravi-hq.github.io/agent-on-demand**

Agent on Demand is a Django service that exposes a REST API for creating **agents**
(model + runtime + MCP servers), **environments** (packages, env vars,
setup scripts, networking), and **sessions** (a single agent execution with
streaming output and multi-turn prompts).

Setting an environment's `networking.type` to `"limited"` enforces a DNS-based
allow-list at session start — domains outside `allowed_hosts` are blocked
(DNS `REFUSED`) for the lifetime of the session.

## Setup

```bash
make install       # uv sync --all-extras
make db-up         # start local Postgres (Docker) + run migrations
```

Session execution runs on a separate **worker** process (Procrastinate,
Postgres-backed broker). The web server only accepts requests and enqueues
work; a worker picks them up and runs them.

Run both in separate terminals:

```bash
make dev           # web — serves HTTP on :8777
make worker        # worker — runs session turns
```

`docker-compose.yml` provisions Postgres 16 on `:5432`; the default
`DATABASE_URL` in `config/settings.py` points at that container. Override
`DATABASE_URL` to point at another DB.

Local teardown:

```bash
make db-down       # stop the container
make db-reset      # destroy the volume and re-migrate
```

## API surface

All endpoints live under the root path. See `src/agent_on_demand/urls.py` for
the full route table.

- `GET  /health`
- `POST /agents`, `GET /agents`, `GET /agents/{id}`, `PUT /agents/{id}`,
  `POST /agents/{id}/archive`, `GET /agents/{id}/versions`
- `POST /environments`, `GET /environments`, `GET /environments/{id}`,
  `PUT /environments/{id}`, `POST /environments/{id}/archive`,
  `DELETE /environments/{id}/delete`, `GET /environments/{id}/versions`
- `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`,
  `POST /sessions/{id}/prompt`, `POST /sessions/{id}/terminate`,
  `DELETE /sessions/{id}/delete`, `GET /sessions/{id}/stream` (SSE)

Authentication is via `Authorization: Bearer <token>`.

## Runtimes

Supported runtimes (selected by `runtime` on an agent) and their models.
Model strings follow the canonical `provider/model_id` form:

| Runtime    | Providers                       | Example models                                                                   |
| ---------- | ------------------------------- | -------------------------------------------------------------------------------- |
| `claude`   | `anthropic`                     | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5` |
| `codex`    | `openai`                        | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                  |
| `gemini`   | `google`                        | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                               |
| `opencode` | `anthropic`, `openai`, `google` | any `anthropic/*`, `openai/*`, or `google/*` in the model catalog                |

A model is servable by a runtime whose `providers` set contains the model's
`provider`. The Claude runtime authenticates via an Anthropic API key by
default and falls back to OAuth automatically when the user has a
`runtime_token:claude-oauth` credential registered — the former `claude-oauth`
runtime was folded into `claude`.

`opencode` is a multi-provider meta-runtime: one `opencode` CLI fronts many
providers and picks provider+model per invocation via `--model
provider/model_id`. It reads the native provider env vars
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`) directly, which
are sourced from the user's registered `UserCredential` rows. Opencode is
**not pre-installed** on the Sprite base image — sessions install it via
`npm i -g opencode-ai` during the `install_runtime` provisioning stage (which
runs before any network policy is applied, so `registry.npmjs.org` does not
need to be in `allowed_hosts`). First-session provisioning takes ~10–30s
longer than the pre-baked runtimes as a result.

Runtime list: `src/agent_on_demand/runtimes/`. Model catalog:
`src/agent_on_demand/models_catalog.py`.

## Tools

Agents do not have a configurable tool allowlist. Each session runs its runtime
CLI with that CLI's full default tool set — `bash`, `read`, `write`, `edit`,
`glob`, `grep`, `web_fetch`, `web_search`, etc. Any MCP servers configured on
the agent are additionally exposed to the runtime. This applies to every
runtime (`claude`, `codex`, `gemini`, `opencode`).

There is no per-agent way to disable or restrict individual built-in tools.

## Testing

### Unit / integration tests

```bash
make test          # runs tests/, excludes tests/e2e
```

### End-to-end tests

The `tests/e2e/` suite hits a running agent-on-demand deployment via HTTP. It
covers agent CRUD + versioning + archive, MCP server validation, environment
CRUD and session integration (packages installed, env vars exported, setup
scripts executed), and the full session lifecycle: streaming (SSE
`start`/`output`/`exit`), termination semantics (409s on re-terminate,
prompt-after-terminate), stream replay after completion, and multi-turn state
via `POST /sessions/{id}/prompt`.

Required env vars:

- `AOD_API_TOKEN` — valid API key for a preconfigured user

Optional:

- `AOD_API_URL` — defaults to `http://localhost:8777` (what `make dev`
  serves). Export a different value to point at another deployment. Note that
  running `pytest` directly (without `make`) defaults to `http://localhost:8000`
  via `tests/e2e/conftest.py`.
- `E2E_RUNTIMES` — comma-separated subset of `claude,codex,gemini,opencode`
  (default `claude`)
- `E2E_TIMEOUT` — max seconds to wait for a session (default `180`)

Run them:

```bash
# fast subset — skips @slow tests that spawn real agent sessions
AOD_API_TOKEN=<token> make test-e2e-fast

# full suite
AOD_API_TOKEN=<token> make test-e2e

# single class
AOD_API_TOKEN=<token> \
  uv run pytest tests/e2e/test_sessions.py::TestStreaming -v

# point at a remote deployment instead of local
AOD_API_URL=https://aod.example.com AOD_API_TOKEN=<token> make test-e2e
```

Without `AOD_API_TOKEN`, every e2e test is auto-skipped by a hook in
`tests/e2e/conftest.py`, so `make test-all` is safe in CI without creds.

## Lint / format

```bash
make lint
make fmt
```

## Layout

```
src/
  config/            Django project (settings, root urls, wsgi)
  agent_on_demand/   App: models, views, auth, runtimes, stream (SSE replay)
    session_service/ Sprites orchestration + the execute_turn Procrastinate task
tests/
  test_*.py          Unit + integration tests (Django test client)
  e2e/               End-to-end tests against a running deployment
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
