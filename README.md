# Fairy

API for running AI coding agents on Sprites.

Fairy is a Django service that exposes a REST API for creating **agents**
(model + runtime + tools + MCP servers), **environments** (packages, env vars,
setup scripts, networking), and **sessions** (a single agent execution with
streaming output and multi-turn prompts).

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
- `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/prompt`,
  `POST /sessions/{id}/terminate`, `DELETE /sessions/{id}/delete`,
  `GET /sessions/{id}/stream` (SSE)

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

Agents accept a `tools` field modeled on Anthropic's Managed Agents
`agent_toolset_20260401` spec. Canonical tool names are `bash`, `read`, `write`,
`edit`, `glob`, `grep`, `web_fetch`, `web_search`. Each runtime translates this
spec to its own CLI flags / config files.

### Enforcement per runtime

| Canonical tool | claude / claude-oauth | codex                            | gemini                                       |
| -------------- | --------------------- | -------------------------------- | -------------------------------------------- |
| `bash`         | ✓                     | not enforceable                  | ✓                                            |
| `read`         | ✓                     | not enforceable                  | ✓                                            |
| `write`        | ✓                     | ✓ (only when `edit` also off)    | ✓ (denies both `write_file` and `replace`)   |
| `edit`         | ✓                     | not enforceable                  | ✓                                            |
| `glob`         | ✓                     | not enforceable                  | ✓                                            |
| `grep`         | ✓                     | not enforceable                  | ✓                                            |
| `web_fetch`    | ✓                     | no codex equivalent (no-op)      | ✓                                            |
| `web_search`   | ✓                     | ✓                                | ✓                                            |

Configurations are accepted silently even when unenforceable on the selected
runtime. For full behavioral guarantees, use `claude` or `gemini`; `codex` is
best-effort.

### MCP servers and mcp_toolset

Agents also accept `mcp_servers` (an array of URL/stdio MCP endpoints) and
`mcp_toolset` entries inside `tools` (per-server allow/deny lists). The
runtime translation surface:

| Scenario | claude / claude-oauth | codex | gemini |
| --- | --- | --- | --- |
| Server declared, no restriction | ✓ (always) | ✓ (always) | ✓ (always) |
| `mcp_toolset default_config.enabled=false` (deny server) | ✓ via `~/.claude/settings.json` `permissions.deny` | ✓ via `enabled = false` on `[mcp_servers.<name>]` | ✓ via `includeTools=[]` |
| `mcp_toolset configs[].enabled=false` (deny one tool) | ✓ via `permissions.deny` with `mcp__<server>__<tool>` | ✓ via `disabled_tools` | ✓ via `excludeTools` |
| `default_config.enabled=false` + allow one tool | ✓ (deny server + allow specific) | ✓ via `enabled_tools` | ✓ via `includeTools` |

Claude MCP tool names in `--disallowedTools` are silently ignored in `-p` mode
(upstream [claude-code#12863](https://github.com/anthropics/claude-code/issues/12863)),
so Fairy writes `~/.claude/settings.json` `permissions.deny` rules instead.
Restrictions reapply on every `POST /sessions/{id}/prompt` because the wrapper
script is rebuilt per call.

Unknown `mcp_toolset.mcp_server_name` values — referencing a server not in the
agent's `mcp_servers` — are rejected with `422`.

See [`docs/tools-and-mcp-examples.md`](docs/tools-and-mcp-examples.md) for
copy-pasteable curl examples of agents configured with tools and MCP
servers.

## Testing

### Unit / integration tests

```bash
make test          # runs tests/, excludes tests/e2e
```

### End-to-end tests

The `tests/e2e/` suite hits a running Fairy deployment via HTTP. It covers
agent CRUD + versioning + archive, tools & MCP server validation,
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

# tool-enforcement matrix — ~11 sessions verifying tools flow through per runtime
FAIRY_API_TOKEN=<token> E2E_RUNTIMES=claude,codex,gemini make test-e2e-tools

# MCP-enforcement matrix — ~14 sessions verifying mcp_servers + mcp_toolset
# are respected. Fairy must be running with DEBUG=True or FAIRY_TESTING=1 so
# the /test-mcp endpoint is live; set MCP_TEST_URL to point at a different
# test server.
FAIRY_API_TOKEN=<token> E2E_RUNTIMES=claude,codex,gemini make test-e2e-mcp

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
