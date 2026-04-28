# Agent on Demand

A REST API for running AI coding agents — Claude Code, Codex, Gemini CLI, opencode — on
fresh, sandboxed cloud machines (Sprites). You POST a prompt, stream the output over SSE,
and optionally continue with follow-up turns. Everything is versioned and auditable.

**Hosted API:** https://aod.ravi.id — sign up, bring your own model API keys, start
running sessions in minutes.

**Full docs:** https://ravi-hq.github.io/agent-on-demand

## What it is

Agent on Demand manages three resources:

- **Agents** — reusable templates: runtime, model, system prompt, MCP servers, optional default environment.
- **Environments** — sandbox config: packages, env vars (encrypted at rest), setup scripts, DNS allow-list.
- **Sessions** — one agent execution. Async; output streams over SSE. Multi-turn via `POST /sessions/{id}/prompt`.

Session state machine: `pending → running → completed | failed | terminated`.

## Quickstart

```bash
BASE=https://aod.ravi.id   # or http://localhost:8777 for local dev
TOKEN=aod_...              # get one at aod.ravi.id after signing up

# 1. Create an agent.
AGENT=$(curl -s -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hello","model":"anthropic/claude-sonnet-4-6","runtime":"claude"}' | jq -r .id)

# 2. Start a session.
SESSION=$(curl -s -X POST "$BASE/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT\",\"prompt\":\"Say hello.\",\"timeout\":120}" | jq -r .id)

# 3. Stream output.
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/$SESSION/stream"
```

Every event is a JSON line on `data:`. `type` tells you what it is: `start`, `stage`,
`output`, `exit`. See [Streaming](https://ravi-hq.github.io/agent-on-demand/api/streaming/)
for the full shape.

## Runtimes

Model strings use the canonical `provider/model_id` form.

| Runtime    | Providers                       | Example models                                                                    |
| ---------- | ------------------------------- | --------------------------------------------------------------------------------- |
| `claude`   | `anthropic`                     | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5` |
| `codex`    | `openai`                        | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                   |
| `gemini`   | `google`                        | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                                |
| `opencode` | `anthropic`, `openai`, `google` | any `anthropic/*`, `openai/*`, or `google/*` in the model catalog                 |

`opencode` is installed at session start (`npm i -g opencode-ai`); first-session
provisioning runs ~10–30 s longer than the pre-baked runtimes.

Runtime source: `src/agent_on_demand/runtimes/`. Model catalog: `src/agent_on_demand/models_catalog.py`.

## Self-hosting

```bash
make install       # uv sync --all-extras
make db-up         # start local Postgres (Docker) + run migrations
```

Session execution runs on a separate **worker** process (Procrastinate,
Postgres-backed). Run both in separate terminals:

```bash
make dev           # web — serves HTTP on :8777
make worker        # worker — runs session turns
```

`docker-compose.yml` provisions Postgres 16 on `:5432`. Override `DATABASE_URL` to
point at another DB.

```bash
make db-down       # stop the container
make db-reset      # destroy the volume and re-migrate
```

For a production deployment (env vars, gunicorn, Render) see the
[Deploy Guide](https://ravi-hq.github.io/agent-on-demand/operators/deploy/).

## API surface

All endpoints live under the root path. Authentication: `Authorization: Bearer <token>`.

- `GET  /health`
- `POST /agents`, `GET /agents`, `GET /agents/{id}`, `PUT /agents/{id}`,
  `POST /agents/{id}/archive`, `GET /agents/{id}/versions`
- `POST /environments`, `GET /environments`, `GET /environments/{id}`,
  `PUT /environments/{id}`, `POST /environments/{id}/archive`,
  `DELETE /environments/{id}/delete`, `GET /environments/{id}/versions`
- `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`,
  `POST /sessions/{id}/prompt`, `POST /sessions/{id}/terminate`,
  `DELETE /sessions/{id}/delete`, `GET /sessions/{id}/stream` (SSE)

Full route table: `src/agent_on_demand/urls.py`. Interactive reference:
https://ravi-hq.github.io/agent-on-demand/api/reference/

## Testing

```bash
make test          # unit + integration (excludes e2e)
```

End-to-end tests hit a running deployment:

```bash
# fast subset — skips @slow tests that spawn real agent sessions
AOD_API_TOKEN=<token> make test-e2e-fast

# full suite
AOD_API_TOKEN=<token> make test-e2e

# point at a remote deployment
AOD_API_URL=https://aod.ravi.id AOD_API_TOKEN=<token> make test-e2e
```

Without `AOD_API_TOKEN`, every e2e test auto-skips, so `make test-all` is safe in CI.

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
clients/
  python/            aod-sdk (PyPI)
  typescript/        @ravi-hq/aod-sdk (npm)
examples/
  cli/               CLI wrapper reference implementation
  chat-bot/          Slack bot with multi-turn sessions
  dashboard/         Web dashboard with SSE proxy
  batch-automation/  Concurrent batch runs with AsyncClient
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
