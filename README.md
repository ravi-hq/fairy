# Fairy

API for running AI coding agents on Sprites.

Fairy is a Django service that exposes a REST API for creating **agents**
(model + runtime + MCP servers), **environments** (packages, env vars,
setup scripts, networking), and **sessions** (a single agent execution with
streaming output and multi-turn prompts).

Setting an environment's `networking.type` to `"limited"` enforces a DNS-based
allow-list at session start — domains outside `allowed_hosts` are blocked
(DNS `REFUSED`) for the lifetime of the session.

## Setup

```bash
make install       # uv sync --all-extras
```

Run the dev server on `:8777`:

```bash
make dev
```

## API surface

All endpoints live under the root path. See `src/fairy/urls.py` for the full
route table.

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

Supported runtimes (selected by `runtime` on an agent) and their model
families:

| Runtime        | Example models                                 |
| -------------- | ---------------------------------------------- |
| `claude`       | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` |
| `claude-oauth` | same as `claude`, OAuth auth path              |
| `codex`        | `gpt-4.1`, `o3`, `o4-mini`                     |
| `gemini`       | `gemini-2.5-pro`, `gemini-2.5-flash`           |

Full list in `src/fairy/runtimes.py`.

## Tools

Agents do not have a configurable tool allowlist. Each session runs its runtime
CLI with that CLI's full default tool set — `bash`, `read`, `write`, `edit`,
`glob`, `grep`, `web_fetch`, `web_search`, etc. Any MCP servers configured on
the agent are additionally exposed to the runtime. This applies to every
runtime (`claude`, `claude-oauth`, `codex`, `gemini`).

There is no per-agent way to disable or restrict individual built-in tools.

## Testing

### Unit / integration tests

```bash
make test          # runs tests/, excludes tests/e2e
```

### End-to-end tests

The `tests/e2e/` suite hits a running Fairy deployment via HTTP. It covers
agent CRUD + versioning + archive, MCP server validation,
environment CRUD and session integration (packages installed, env vars
exported, setup scripts executed), and the full session lifecycle:
streaming (SSE `start`/`output`/`exit`), termination semantics (409s on
re-terminate, prompt-after-terminate), stream replay after completion,
and multi-turn state via `POST /sessions/{id}/prompt`.

Required env vars:

- `FAIRY_API_TOKEN` — valid API key for a preconfigured user

Optional:

- `FAIRY_API_URL` — defaults to `http://localhost:8777` (what `make dev`
  serves). Export a different value to point at another deployment. Note that
  running `pytest` directly (without `make`) defaults to `http://localhost:8000`
  via `tests/e2e/conftest.py`.
- `E2E_RUNTIMES` — comma-separated subset of `claude,codex,gemini,claude-oauth`
  (default `claude`)
- `E2E_TIMEOUT` — max seconds to wait for a session (default `180`)

Run them:

```bash
# fast subset — skips @slow tests that spawn real agent sessions
FAIRY_API_TOKEN=<token> make test-e2e-fast

# full suite
FAIRY_API_TOKEN=<token> make test-e2e

# single class
FAIRY_API_TOKEN=<token> \
  uv run pytest tests/e2e/test_sessions.py::TestStreaming -v

# point at a remote deployment instead of local
FAIRY_API_URL=https://fairy.example.com FAIRY_API_TOKEN=<token> make test-e2e
```

Without `FAIRY_API_TOKEN`, every e2e test is auto-skipped by a hook in
`tests/e2e/conftest.py`, so `make test-all` is safe in CI without creds.

## Lint / format

```bash
make lint
make fmt
```

## Layout

```
src/
  config/          Django project (settings, root urls, wsgi)
  fairy/           App: models, views, auth, runtimes, sprites_exec, stream
tests/
  test_*.py        Unit + integration tests (Django test client)
  e2e/             End-to-end tests against a running deployment
```
