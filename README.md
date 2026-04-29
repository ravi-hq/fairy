# Agent on Demand

A REST API for running AI coding agents on [Sprites](https://sprites.dev) — lightweight,
fast-booting cloud sandboxes. Define an agent (model + runtime + system prompt), wire up
an environment (packages, env vars, setup script, network policy), then `POST /sessions`
and stream the output over SSE. Multi-turn conversations resume in the same Sprite with
the same filesystem.

Three resources, one workflow: **agent → session → stream**.

**Hosted API:** https://aod.ravi.id — sign up, bring your own model API keys, start
running sessions in minutes.

**Full docs:** https://ravi-hq.github.io/agent-on-demand

## Quickstart

```bash
BASE=https://aod.ravi.id   # or http://localhost:8777 for local dev
TOKEN=aod_...              # get one at aod.ravi.id after signing up

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

## SDKs

Official client libraries for common runtimes:

- **Python** ([`aod-sdk`](https://pypi.org/p/aod-sdk)): sync + async, typed SSE stream, pydantic models — `pip install aod-sdk`
- **TypeScript/Node** ([`@ravi-hq/aod-sdk`](https://www.npmjs.com/package/@ravi-hq/aod-sdk)): zero-dep, async-iterable SSE stream — `npm install @ravi-hq/aod-sdk`

See [`clients/python/`](clients/python/) and [`clients/typescript/`](clients/typescript/) for source and full API surface.

## Self-hosting

```bash
make install       # uv sync --all-extras
make db-up         # start local Postgres (Docker) + run migrations
```

Session execution runs on a separate **worker** process (Procrastinate, Postgres-backed).
The web server accepts requests and enqueues work; the worker runs them.

Run both in separate terminals:

```bash
make dev           # web — serves HTTP on :8777
make worker        # worker — runs session turns
```

`docker-compose.yml` provisions Postgres 16 on `:5432`. Override `DATABASE_URL` to point
at another DB.

```bash
make db-down       # stop the container
make db-reset      # destroy the volume and re-migrate
```

For a production deployment (env vars, gunicorn, Render) see the
[Deploy Guide](https://ravi-hq.github.io/agent-on-demand/operators/deploy/).

## API surface

All endpoints live under the root path. See `src/agent_on_demand/urls.py` for the full
route table. Authentication: `Authorization: Bearer <token>`.

- `GET  /health`
- `POST /agents`, `GET /agents`, `GET /agents/{id}`, `PUT /agents/{id}`,
  `POST /agents/{id}/archive`, `GET /agents/{id}/versions`
- `POST /environments`, `GET /environments`, `GET /environments/{id}`,
  `PUT /environments/{id}`, `POST /environments/{id}/archive`,
  `DELETE /environments/{id}/delete`, `GET /environments/{id}/versions`
- `POST /sessions`, `GET /sessions`, `GET /sessions/{id}`,
  `POST /sessions/{id}/prompt`, `POST /sessions/{id}/terminate`,
  `DELETE /sessions/{id}/delete`, `GET /sessions/{id}/stream` (SSE)

## Runtimes

Model strings are in canonical `provider/model_id` form:

| Runtime    | Vendors                   | Example model strings                                                              |
| ---------- | ------------------------- | ---------------------------------------------------------------------------------- |
| `claude`   | Anthropic                 | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5` |
| `codex`    | OpenAI                    | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                    |
| `gemini`   | Google                    | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                                 |
| `opencode` | Anthropic, OpenAI, Google | any `anthropic/*`, `openai/*`, or `google/*` in the model catalog                  |

The `claude` runtime authenticates via `ANTHROPIC_API_KEY` by default and falls back to
OAuth when the user has a `runtime_token:claude-oauth` credential registered.

`opencode` is not pre-installed on the Sprite base image — first-session provisioning
takes 10–30 s longer than the pre-baked runtimes.

Model catalog: `src/agent_on_demand/models_catalog.py`. Runtime implementations:
`src/agent_on_demand/runtimes/`.

## Tools

Agents do not have a configurable tool allowlist. Each session runs its runtime CLI with
that CLI's full default tool set — `bash`, `read`, `write`, `edit`, `glob`, `grep`,
`web_fetch`, `web_search`, etc. Any MCP servers configured on the agent are additionally
exposed to the runtime. There is no per-agent way to disable or restrict individual
built-in tools.

## Testing

```bash
make test          # unit + integration (excludes e2e)
```

End-to-end tests hit a running deployment:

```bash
AOD_API_TOKEN=<token> make test-e2e-fast   # skips @slow tests that spawn real sessions
AOD_API_TOKEN=<token> make test-e2e        # full suite
AOD_API_URL=https://aod.ravi.id AOD_API_TOKEN=<token> make test-e2e
```

Without `AOD_API_TOKEN`, every e2e test auto-skips — `make test-all` is safe in CI.

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
