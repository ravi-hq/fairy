---
date: 2026-04-16T12:00:00-07:00
researcher: Claude Code (team-research skill)
git_commit: n/a (not a git repo)
branch: n/a
repository: local
topic: "E2E test suite design for Fairy API"
tags: [research, team-research, e2e, testing]
status: complete
method: direct-research
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: E2E Test Suite Design for Fairy API

**Date**: 2026-04-16
**Researcher**: Claude Code (direct research — full codebase read)

## Research Question

Design an e2e test suite where the caller provides a Fairy API token and tests: sessions for all runtimes, multi-turn conversations, streaming, termination, environment setup (packages, env vars, setup script), and agent configuration.

## Summary

Fairy is a Django API that runs AI coding agents on Sprites (sandboxed containers). The existing test suite mocks all Sprite interactions — no real agent execution is tested. An e2e test suite needs a live Fairy deployment, a valid API key, and runtime keys configured for each runtime (claude, codex, gemini, claude-oauth). Tests should verify the full lifecycle: create agent → create session → stream output → send follow-up prompt → terminate, plus environment setup verification.

## Track 1: API Surface & Auth

### Endpoints to test:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/agents` | Create agent |
| GET | `/agents` | List agents |
| GET | `/agents/{id}` | Get agent |
| PUT | `/agents/{id}` | Update agent |
| POST | `/agents/{id}/archive` | Archive agent |
| GET | `/agents/{id}/versions` | List versions |
| POST | `/environments` | Create environment |
| GET | `/environments` | List environments |
| GET | `/environments/{id}` | Get environment |
| PUT | `/environments/{id}` | Update environment |
| POST | `/environments/{id}/archive` | Archive environment |
| DELETE | `/environments/{id}/delete` | Delete environment |
| GET | `/environments/{id}/versions` | List versions |
| POST | `/sessions` | Create session |
| GET | `/sessions/{id}` | Get session |
| GET | `/sessions/{id}/stream` | Stream SSE |
| POST | `/sessions/{id}/prompt` | Multi-turn |
| POST | `/sessions/{id}/terminate` | Terminate |
| DELETE | `/sessions/{id}/delete` | Delete session |

### Auth (`src/fairy/auth.py`)
- Bearer token in `Authorization` header
- Looked up by SHA-256 hash
- Checks `is_active` and `expires_at`

## Track 2: Session Lifecycle & Runtimes

### Runtimes (`src/fairy/runtimes.py`):
- **claude**: `claude --print --verbose --output-format stream-json -p "$PROMPT"` / env var: `ANTHROPIC_API_KEY`
- **codex**: `echo "$PROMPT" | codex exec --full-auto --json` / env var: `CODEX_API_KEY`
- **gemini**: `gemini --output-format stream-json -p "$PROMPT"` / env var: `GEMINI_API_KEY`
- **claude-oauth**: Same as claude but env var: `CLAUDE_CODE_AUTH_TOKEN`

### Session states:
`pending` → `running` → `completed` (exit 0) / `failed` (non-zero or error) / `terminated`

### Multi-turn (`src/fairy/views.py:335-404`):
- `POST /sessions/{id}/prompt` sends follow-up
- Rewrites `run-agent.sh` with `continue_cmd` (e.g., `--continue` flag)
- Rejects if session is `running` or `terminated`
- Re-uses the same Sprite (persistent filesystem)

### Streaming (`src/fairy/stream.py:131-174`):
- SSE format: `data: {json}\n\n`
- Event types: `start`, `output` (stdout/stderr), `exit`, `error`, `terminated`
- Heartbeat every 15s (empty comment line)
- Polls DB every 500ms for new log rows

### Termination (`src/fairy/views.py:407-435`):
- Deletes the Sprite
- Sets status to `terminated`, clears `sprite_name`

## Track 3: Environments

### Environment model (`src/fairy/models.py:141-180`):
- `packages`: dict of `{manager: [pkg_list]}` — managers: apt, cargo, gem, go, npm, pip
- `env_vars`: dict of `{key: value}` — exported in wrapper script
- `setup_script`: arbitrary bash script run after packages/clone
- `networking_type`: `unrestricted` or `limited`

### Wrapper script order (`src/fairy/sprites_exec.py:218-272`):
1. Export API key
2. Export env vars
3. Setup working dir + git init
4. Install packages (in alphabetical manager order)
5. Clone repos
6. Run setup script
7. Write MCP config
8. `exec` the runtime command

### E2E verification approach:
- Create environment with packages + env vars + setup script
- Create session using that environment
- The agent prompt should ask it to verify: run `which <package>`, `echo $ENV_VAR`, check setup script side effects

## Track 4: Agents

### Agent fields (`src/fairy/models.py:208-239`):
- `name`, `description`, `system` (system prompt), `model`, `runtime`
- `environment` (FK, optional)
- `skills` (JSON list), `tools` (JSON list), `mcp_servers` (JSON list)
- `metadata` (JSON dict, merge semantics on update)
- `version` (optimistic concurrency)

### Valid models (`src/fairy/runtimes.py:5-28`):
Claude family (opus-4-6, sonnet-4-6, haiku-4-5, opus-4, sonnet-4, sonnet-4-5, haiku-3-5), OpenAI (gpt-4.1, o3, o4-mini), Gemini (2.5-pro, 2.5-flash)

### Tools validation (`src/fairy/views.py:463-485`):
- `agent_toolset_20260401` — built-in toolset
- `mcp_toolset` — requires `mcp_server_name`
- `custom` — requires `name`, `description`, `input_schema`

### MCP servers validation (`src/fairy/views.py:488-509`):
- Types: `url` (requires `url` field), `stdio` (requires `command` field)
- Max 20 servers, no duplicate names
- Config written to runtime-specific format in wrapper script

### System prompt (`src/fairy/views.py:212-214`):
- Prepended to user prompt: `"{system}\n\n{prompt}"`

## Proposed E2E Test Suite Structure

### Configuration
```python
# tests/e2e/conftest.py
# Required env vars:
# FAIRY_API_URL - base URL of live Fairy deployment
# FAIRY_API_TOKEN - valid API key
# Optional (skip tests for runtimes without keys):
# E2E_TEST_CLAUDE - set to "1" to test claude runtime
# E2E_TEST_CODEX - set to "1" to test codex runtime
# E2E_TEST_GEMINI - set to "1" to test gemini runtime
```

### Test Categories

#### 1. Session Lifecycle (per runtime)
- Create agent for runtime → create session → wait for completion → verify exit code 0
- Verify session status transitions: pending → running → completed
- Verify streaming produces start + output + exit events
- Use a simple prompt like "echo hello" to keep fast

#### 2. Multi-Turn Conversations
- Create session → wait for completion → send follow-up prompt → wait again
- Verify the agent has context from the first turn (e.g., "create a file" then "read that file")
- Verify `send_prompt` rejects while session is `running`

#### 3. Streaming
- Connect to SSE stream before session completes
- Verify start event contains runtime + session_id
- Verify output events have stream (stdout/stderr) and data fields
- Verify exit event has exit code
- Verify replay works (stream after completion)

#### 4. Termination
- Create session → terminate while running or after completion
- Verify status becomes `terminated`
- Verify stream returns terminated event
- Verify send_prompt returns 409 on terminated session

#### 5. Environments
- Create environment with `packages: {"pip": ["requests"]}`, `env_vars: {"TEST_VAR": "hello"}`, `setup_script: "echo setup_done > /tmp/setup_marker"`
- Create agent + session using that environment
- Prompt: "Run these commands and report the output: python -c 'import requests; print(requests.__version__)' && echo $TEST_VAR && cat /tmp/setup_marker"
- Verify all three succeed in the output

#### 6. Agents
- CRUD lifecycle: create → get → update → list → archive
- Versioning: update creates new version, list versions returns history
- System prompt: create agent with system prompt → verify it appears in session output
- Tools + MCP: create agent with tools/mcp_servers → verify session wrapper script uses them
- Environment inheritance: agent with default environment → session inherits it
- Metadata merge semantics: update with `""` deletes key

### Test Helpers Needed
- `wait_for_session(session_id, timeout)` — poll `GET /sessions/{id}` until terminal state
- `collect_stream(session_id)` — consume SSE stream, return list of events
- `create_test_agent(runtime, **overrides)` — shorthand for agent creation
- `cleanup_session(session_id)` — terminate + delete (fixture finalizer)

## Open Questions

1. **Runtime key setup**: E2E tests need UserRuntimeKey entries. The API doesn't expose a CRUD endpoint for runtime keys — these must be pre-configured in the database. Should we add an admin/setup endpoint, or use Django management commands?
2. **Cost control**: Real agent sessions cost API credits. Tests should use minimal prompts and short timeouts (30-60s).
3. **Parallelism**: Can tests run in parallel? Each test creates its own agent/session, but SQLite may bottleneck on concurrent writes.
4. **CI**: How to provide runtime API keys in CI? Environment variables or secrets.
