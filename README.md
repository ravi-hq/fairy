# Agent on Demand

A REST API for running AI coding agents on [Sprites](https://sprites.dev) — lightweight, fast-booting cloud sandboxes. Define an agent (model + runtime + system prompt), wire up an environment (packages, env vars, setup script, network policy), then `POST /sessions` and stream the output over SSE. Multi-turn conversations resume in the same Sprite with the same filesystem.

Three resources, one workflow: **agent → session → stream**.

## Quickstart

```bash
BASE=http://localhost:8777
TOKEN=aod_...

# 1. Create an agent
AGENT_ID=$(curl -s -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hello","model":"anthropic/claude-sonnet-4-6","runtime":"claude"}' \
  | jq -r .id)

# 2. Start a session
SESS_ID=$(curl -s -X POST "$BASE/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"prompt\":\"Say hello.\",\"timeout\":120}" \
  | jq -r .id)

# 3. Stream output (SSE)
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/$SESS_ID/stream"
```

Full docs and SDK reference: **https://ravi-hq.github.io/agent-on-demand**

## Setup

```bash
make install       # uv sync --all-extras
make db-up         # start local Postgres (Docker) + run migrations
```

Session execution runs on a separate **worker** process (Procrastinate, Postgres-backed). The web server accepts requests and enqueues work; the worker runs them.

Run both in separate terminals:

```bash
make dev           # web — serves HTTP on :8777
make worker        # worker — runs session turns
```

`docker-compose.yml` provisions Postgres 16 on `:5432`. Override `DATABASE_URL` to point at another DB.

```bash
make db-down       # stop the container
make db-reset      # destroy the volume and re-migrate
```

## API surface

All endpoints live under the root path. See `src/agent_on_demand/urls.py` for the full route table.

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
Model strings are in canonical `provider/model_id` form:

| Runtime    | Vendors                         | Example model strings                                                              |
| ---------- | ------------------------------- | ---------------------------------------------------------------------------------- |
| `claude`   | Anthropic                       | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5` |
| `codex`    | OpenAI                          | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                    |
| `gemini`   | Google                          | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                                 |
| `opencode` | Anthropic, OpenAI, Google       | any `anthropic/*`, `openai/*`, or `google/*` in the model catalog                  |

The `claude` runtime authenticates via `ANTHROPIC_API_KEY` by default and falls back to OAuth when the user has a `runtime_token:claude-oauth` credential registered.

`opencode` is a multi-provider meta-runtime (sst/opencode) that is not pre-installed on the Sprite base image — first-session provisioning takes 10–30 s longer than the pre-baked runtimes.

Full list: `src/agent_on_demand/models_catalog.py` (models), `src/agent_on_demand/runtimes/` (runtime implementations).

## Tools

Agents do not have a configurable tool allowlist. Each session runs its runtime CLI with that CLI's full default tool set — `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`, etc. Any MCP servers configured on the agent are additionally exposed to the runtime.

There is no per-agent way to disable or restrict individual built-in tools.

## Testing

### Unit / integration tests

```bash
make test          # runs tests/, excludes tests/e2e
```

### End-to-end tests

The `tests/e2e/` suite hits a running deployment via HTTP. It covers agent CRUD + versioning + archive, environment CRUD and session integration, and the full session lifecycle (streaming, termination, multi-turn via `POST /sessions/{id}/prompt`).

Required env vars:

- `AOD_API_TOKEN` — valid API key for a preconfigured user

Optional:

- `AOD_API_URL` — defaults to `http://localhost:8777`
- `E2E_RUNTIMES` — comma-separated subset of `claude,codex,gemini,opencode` (default `claude`)
- `E2E_TIMEOUT` — max seconds to wait for a session (default `180`)

```bash
AOD_API_TOKEN=<token> make test-e2e-fast   # skips @slow tests that spawn real sessions
AOD_API_TOKEN=<token> make test-e2e        # full suite
AOD_API_URL=https://aod.example.com AOD_API_TOKEN=<token> make test-e2e
```

Without `AOD_API_TOKEN`, every e2e test auto-skips, so `make test-all` is safe in CI without creds.

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
