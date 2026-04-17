# Fairy API — Operator Guide for Claude Code Agents

This is a plain-text, copy-paste-ready guide to driving the Fairy API end to
end. Audience: an autonomous coding agent (like Claude Code) that will read
this once and then make real HTTP calls. It is organized so you can stop
reading as soon as you have what you need.

Table of contents:

1. Quickstart
2. Conventions (auth, errors, versioning, metadata, IDs, timestamps)
3. Runtime & model matrix
4. Agents — setup and lifecycle
5. Environments — setup and lifecycle
6. Sessions — create, stream, multi-turn, terminate, delete
7. End-to-end worked example
8. Error code reference
9. Full route table

---

## 1. Quickstart

Base URL (local dev): `http://localhost:8777`
Base URL (prod): set via your deployment.

Auth: `Authorization: Bearer fairy_<token>`. Tokens are created server-side
via `APIKey.create_key(user, name)`; the raw string is only shown once, at
creation. Every string you see that starts with `fairy_` is an API token.

Minimum viable "hello world":

```bash
BASE=http://localhost:8777
TOKEN=fairy_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 1. Check the server is up (no auth needed).
curl "$BASE/health"
# → 200 {"status":"ok"}

# 2. Create an agent (requires a valid model + runtime).
curl -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hello","model":"claude-sonnet-4-6","runtime":"claude"}'
# → 201 {"id":"<agent-uuid>","version":1,...}

# 3. Create a session to run a prompt on that agent.
curl -X POST "$BASE/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"<agent-uuid>","prompt":"Say hello.","timeout":120}'
# → 202 {"id":"<sess-uuid>","status":"pending","stream_url":"/sessions/<sess-uuid>/stream",...}

# 4. Stream output until it ends.
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/<sess-uuid>/stream"
```

That is the whole critical path. Everything else is variation on these three
resources.

---

## 2. Conventions

### 2.1 Authentication

- Every request except `GET /health` requires `Authorization: Bearer <token>`.
- Any auth failure returns **401** with a string `detail`:
  - `{"detail":"Missing or invalid Authorization header"}`
  - `{"detail":"Invalid API key"}`
  - `{"detail":"API key is inactive"}`
  - `{"detail":"API key has expired"}`
- There is no 403 in this API.

### 2.2 Request/response format

- JSON in, JSON out. Send `Content-Type: application/json` on bodies.
- List endpoints return `{"data":[...]}`. Single-resource endpoints return the
  object directly (no envelope).
- **No pagination. No filtering.** `GET /agents` and `GET /environments`
  always return the full non-archived set ordered by `-created_at`. `GET
  /sessions` does **not exist** — sessions are accessed by ID only. Track
  session IDs yourself.
- Archived resources are hidden from list endpoints but remain accessible via
  `GET /{resource}/{id}`.

### 2.3 Error envelope

One universal shape: `{"detail": <string or list>}`.

- For **every** HTTP status EXCEPT 422, `detail` is a string.
- For **422 (Pydantic validation failure)**, `detail` is a list of error
  objects: `[{"type":"missing","loc":["prompt"],"msg":"Field required","input":{}}]`.
- If you write a client, branch on `isinstance(detail, list)`.

### 2.4 Optimistic concurrency (agents & environments)

Every agent and environment has an integer `version` that starts at 1 and
increments on each mutating `PUT`. When you update, you must echo the
current `version` in the body:

```json
{"version": 1, "name": "new-name"}
```

- If the version matches: the write succeeds, `version` becomes `N+1`, a row
  is written to `{agent,environment}_versions`. A no-op PUT (values identical
  to current) does **not** bump the version.
- If the version is stale: `409 {"detail": "Version mismatch: expected 3, got 2"}`.
  Re-fetch, re-apply your changes against the new state, retry.

Sessions do **not** have a version.

### 2.5 Metadata (agents only)

Agents carry a flat `metadata: {string: string}`. Environments do **not** have
a metadata field.

Merge semantics on PUT:

- Key with a non-empty string → upsert (replace or insert the value).
- Key with `""` (empty string) → **delete** that key.
- Key omitted from the payload → unchanged.

Example: current metadata `{"team":"platform","env":"prod"}` + PUT
`{"version":2,"metadata":{"env":"staging","team":""}}` → result
`{"env":"staging"}`.

Contrast with environment `env_vars`, which uses **full replacement**
(see §5).

### 2.6 Timestamps & IDs

- All timestamps: ISO 8601 with UTC offset, e.g. `2026-04-17T14:00:00.000000+00:00`.
- Field names used across resources: `created_at`, `updated_at`,
  `archived_at` (nullable — `null` while active).
- IDs: UUID v4, lowercase, server-assigned. Never sent by the client.

### 2.7 Idempotency & conflict rules (409 cheat sheet)

All of these return **409**:

- Archive an already-archived agent/environment.
- PUT an archived agent/environment (`"Cannot update an archived ..."`).
- Terminate an already-terminated session (`"Session is already terminated"`).
- POST `/sessions/{id}/prompt` on a running session (`"Session is already running"`).
- POST `/sessions/{id}/prompt` on a terminated session (`"Session has been terminated"`).
- DELETE a running session (`"Cannot delete a running session"`).
- DELETE an environment that has any sessions referencing it (`"Cannot delete environment with existing sessions"`).
- Create a session with an archived agent or environment.

Delete-of-missing → **404**. Deletes return `200 {"detail":"... deleted"}`.
There is no 204.

---

## 3. Runtime & model matrix

Pick one `runtime` and one `model` per agent. The API does **not** cross-check
them — you can save `model="claude-opus-4-6"` with `runtime="gemini"` and it
will 201. The session will fail at execution time. Always pair correctly:

| Runtime        | Auth path                              | Valid models                                                                                                              |
| -------------- | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `claude`       | Anthropic API key                      | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-0-20250514`, `claude-sonnet-4-0-20250514`, `claude-sonnet-4-5-20250514`, `claude-3-5-haiku-20241022` |
| `claude-oauth` | Anthropic OAuth                        | same set as `claude`                                                                                                      |
| `codex`        | OpenAI API key                         | `gpt-4.1`, `o3`, `o4-mini`                                                                                                |
| `gemini`       | Google API key                         | `gemini-2.5-pro`, `gemini-2.5-flash`                                                                                      |

Source of truth: `src/fairy/runtimes.py`. If you see `{"detail":"No API key
configured for runtime: <name>"}` at session create, the server user has no
runtime key for that runtime.

---

## 4. Agents — setup and lifecycle

An **agent** is a reusable template: model + runtime + system prompt + skills
+ MCP servers + (optionally) a default environment + metadata.

### 4.1 Create an agent

```
POST /agents
Authorization: Bearer fairy_<token>
Content-Type: application/json
```

Body fields:

| Field            | Type                  | Required | Default | Notes |
|------------------|-----------------------|----------|---------|-------|
| `name`           | string (≤200)         | yes      | —       | |
| `model`          | string                | yes      | —       | Must be a valid model (see §3). Invalid → 422 list |
| `runtime`        | string                | yes      | —       | One of `claude`, `claude-oauth`, `codex`, `gemini`. Invalid → 400 string |
| `system`         | string                | no       | `""`    | System prompt. See §4.5 for multi-turn caveat |
| `description`    | string                | no       | `""`    | |
| `environment_id` | UUID string \| null   | no       | null    | Default environment (session create can override) |
| `skills`         | array of skill objects| no       | `[]`    | See `thoughts/research/2026-04-17-agent-skills-support.md` |
| `mcp_servers`    | array of MCP objects  | no       | `[]`    | See §4.4 |
| `metadata`       | object<string,string> | no       | `{}`    | Flat, merge semantics on PUT |

Unknown fields are silently dropped by Pydantic.

Response: **201** with the full agent object (see §4.2 for shape).

### 4.2 Agent object shape (create/get/list response)

```json
{
  "id": "3f2a1b4c-...",
  "type": "agent",
  "name": "code-reviewer",
  "description": "Reviews PRs",
  "system": "You are an expert code reviewer.",
  "model": "claude-sonnet-4-6",
  "runtime": "claude",
  "environment_id": null,
  "skills": [],
  "mcp_servers": [],
  "metadata": {"team": "platform"},
  "version": 1,
  "created_at": "2026-04-17T14:00:00+00:00",
  "updated_at": "2026-04-17T14:00:00+00:00",
  "archived_at": null
}
```

`description` and `system` appear as `null` when empty.

### 4.3 List / get / update / archive

- `GET /agents` → `{"data": [<agent>, ...]}` (non-archived only, `-created_at`).
- `GET /agents/{id}` → single object (works even if archived).
- `PUT /agents/{id}` — requires `version`. Mutable fields: `name`, `model`,
  `runtime`, `system`, `description`, `environment_id`, `skills`,
  `mcp_servers`, `metadata`. See §2.4 for versioning and §2.5 for metadata
  semantics. PUT on an archived agent → 409.
- `POST /agents/{id}/archive` — no body. Returns 200 with `archived_at` set.
  Already-archived → 409. No un-archive endpoint.
- `GET /agents/{id}/versions` → `{"data": [<snapshot>, ...]}` newest first.
  Each snapshot has the same fields as the main object minus `updated_at` and
  `archived_at`.

### 4.4 MCP servers

Shape: array of objects. Max 20 per agent. Each object:

```json
{
  "name": "github",              // required, unique within the array
  "type": "url",                 // "url" (default) or "stdio"
  "url": "https://mcp.github.com/mcp",
  "headers": {"X-Foo": "bar"}   // optional
}
```

For `type: "stdio"`:

```json
{
  "name": "filesystem",
  "type": "stdio",
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
  "env": {"DEBUG": "1"}
}
```

MCP servers are materialized into the Sprite once per session, at turn 1.
**Multi-turn continuations do NOT re-apply MCP config** — if you need new
servers mid-session, you must create a new session.

Validation errors from `_validate_mcp_servers` return **422**.

### 4.5 System prompt + multi-turn caveat

When you create a session, the agent's `system` is prepended to the user
prompt *once*: `effective_prompt = f"{agent.system}\n\n{prompt}"`. On
`POST /sessions/{id}/prompt` for multi-turn, the system prompt is **NOT**
re-prepended — it's already in the runtime's session history from turn 1.
Don't duplicate it yourself.

### 4.6 Worked example

```bash
# Create
curl -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "code-reviewer",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "system": "You are an expert code reviewer. Cite file:line.",
    "description": "Reviews PRs for correctness",
    "mcp_servers": [
      {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}
    ],
    "metadata": {"team": "platform", "env": "prod"}
  }'
# → 201 {"id":"<agent-uuid>","version":1,...}

# Update system prompt; rotate metadata (delete "team", change "env").
curl -X PUT "$BASE/agents/<agent-uuid>" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "system": "You are an expert code reviewer. Always cite line numbers and suggest fixes.",
    "metadata": {"env": "staging", "team": ""}
  }'
# → 200 version=2, metadata={"env":"staging"}

# Inspect history
curl -H "Authorization: Bearer $TOKEN" "$BASE/agents/<agent-uuid>/versions"
# → 200 {"data":[{version:2,...},{version:1,...}]}

# Archive (removes from list, still GET-able by ID)
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/agents/<agent-uuid>/archive"
# → 200 archived_at set
# Second archive → 409 "Agent is already archived"
```

---

## 5. Environments — setup and lifecycle

An **environment** describes the Sprite sandbox a session runs in: Linux
packages to install, env vars to export, a bash setup script, and a network
policy.

### 5.1 Create an environment

```
POST /environments
Authorization: Bearer fairy_<token>
Content-Type: application/json
```

Body fields:

| Field           | Type                                | Required | Default                    |
|-----------------|-------------------------------------|----------|----------------------------|
| `name`          | string (≤200)                       | yes      | —                          |
| `packages`      | `{manager: [string, ...]}`          | no       | `{}`                       |
| `env_vars`      | `object<string, string>`            | no       | `{}`                       |
| `setup_script`  | string (multiline)                  | no       | `""`                       |
| `networking`    | `{type, ...}` object                | no       | `{"type": "unrestricted"}` |

Valid package managers: `apt`, `cargo`, `gem`, `go`, `npm`, `pip`. Unknown
manager → 422. Package strings pass through verbatim — `"ripgrep@14.0.0"` is
fine for cargo.

Name uniqueness: `(user, name)` where `archived_at IS NULL`. Archiving frees
the name for reuse.

Response: **201** with the environment object (see §5.2).

### 5.2 Environment object shape (create/get/list response)

```json
{
  "id": "e7a2c4f1-...",
  "type": "environment",
  "name": "ml-sandbox",
  "packages": {"pip": ["torch", "numpy"], "apt": ["ffmpeg"]},
  "setup_script": "pip install -r /workspace/requirements.txt || true",
  "networking": {"type": "limited", "allowed_hosts": ["api.openai.com"]},
  "version": 1,
  "created_at": "2026-04-17T14:00:00+00:00",
  "updated_at": "2026-04-17T14:00:00+00:00",
  "archived_at": null
}
```

**`env_vars` is never returned in any response.** You can set it, you can
replace it via PUT, but you can't read it back from the API. If you need to
verify a value, check it from inside a session (e.g. `echo $MY_VAR`).
`setup_script` is `null` when empty.

### 5.3 Networking

Two modes. Pick one:

```json
{"type": "unrestricted"}
```

Open network — no policy applied to the Sprite.

```json
{"type": "limited", "allowed_hosts": ["api.openai.com", "*.github.com", "pypi.org"]}
```

DNS-based allow-list. Wildcards supported verbatim. Under the hood at session
start: one `allow` rule per host + a trailing `deny *` rule. Any host outside
the list gets DNS `REFUSED` for the life of the session.

Empty `allowed_hosts` + `limited` = deny-all (total network isolation).

Validation errors → 422. If the Sprite network-policy call fails at session
start → `502 {"detail":"Failed to ..."}` and the Sprite is torn down.

### 5.4 env_vars semantics — IMPORTANT

Unlike agent metadata, environment `env_vars` uses **FULL REPLACEMENT** on
PUT. Sending `{"env_vars": {"NEW": "val"}}` replaces the entire dict — any
key you don't include is removed. To add without removing, re-send all keys
you want to keep.

```bash
# Current env_vars = {"A": "1", "B": "2"}

# WRONG — this deletes A.
curl -X PUT "$BASE/environments/<id>" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"version":3,"env_vars":{"B":"2","C":"3"}}'
# Result: {"B":"2","C":"3"}

# RIGHT — resend all keys to preserve them.
curl -X PUT "$BASE/environments/<id>" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"version":3,"env_vars":{"A":"1","B":"2","C":"3"}}'
```

### 5.5 List / get / update / archive / delete

- `GET /environments` → `{"data":[...]}` non-archived, no query params.
- `GET /environments/{id}` → single object (including archived).
- `PUT /environments/{id}` — requires `version`. Mutable: `name`, `packages`,
  `env_vars`, `setup_script`, `networking`. Archived → 409.
- `GET /environments/{id}/versions` → version history, `-version` order.
  `env_vars` is excluded from snapshots too.
- `POST /environments/{id}/archive` — soft archive. Second call → 409.
- `DELETE /environments/{id}/delete` — hard delete. Blocked if any session
  references this env (even terminated ones) → 409
  `"Cannot delete environment with existing sessions"`. Does NOT require prior
  archive. Cascades the version history.

Practical pattern: **prefer archive**. Use hard delete only for
test-created environments with no sessions.

### 5.6 Execution order inside the Sprite

When a session starts using this environment (informational — you don't
control the order):

1. Export runtime API key
2. Export `env_vars`
3. `cd /home/sprite && git init`
4. Install packages: `apt` → `cargo` → `gem` → `go` → `npm` → `pip`
5. Clone any `github_repository` resources from the session request
6. Run `setup_script`
7. Write MCP config
8. Write skills
9. Exec the agent CLI

**None of steps 2–8 re-run on multi-turn `/prompt`** — only the agent CLI is
re-invoked with a `continue_session` flag. If you need to add a package or an
env var mid-conversation, start a new session.

### 5.7 Worked example

```bash
curl -X POST "$BASE/environments" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ml-sandbox",
    "packages": {"pip": ["torch", "numpy"], "apt": ["ffmpeg"]},
    "env_vars": {"OPENAI_API_KEY": "sk-abc123", "LOG_LEVEL": "debug"},
    "setup_script": "pip install -r /workspace/requirements.txt || true",
    "networking": {
      "type": "limited",
      "allowed_hosts": ["api.openai.com", "pypi.org", "files.pythonhosted.org"]
    }
  }'
# → 201 {"id":"<env-uuid>","version":1,...}
# (note: env_vars is NOT in the response)

# Update packages (full replacement of the pip list)
curl -X PUT "$BASE/environments/<env-uuid>" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"version":1,"packages":{"pip":["torch==2.0","numpy","pandas"],"apt":["ffmpeg"]}}'
# → 200 version=2

# Archive when no longer needed
curl -X POST -H "Authorization: Bearer $TOKEN" "$BASE/environments/<env-uuid>/archive"
# → 200
```

---

## 6. Sessions — lifecycle

A **session** is one execution of an agent inside a Sprite. State evolves
through:

```
                   POST /sessions
                        │
                        ▼
                   ┌─────────┐
                   │ pending │◄────────────────────────────────┐
                   └────┬────┘                                 │
                        │ background execution starts          │ POST /sessions/{id}/prompt
                        ▼                                      │ (allowed; state resets)
                   ┌─────────┐                                 │
                   │ running │─────────────────────────────────┘
                   └────┬────┘
          ┌─────────────┼──────────────┐
          │             │              │
          ▼             ▼              ▼
    ┌──────────┐  ┌────────┐  ┌──────────────┐
    │completed │  │ failed │  │  terminated  │◄── POST /sessions/{id}/terminate
    └──────────┘  └────────┘  └──────────────┘       (any non-terminated state)

409 edges:
  POST /prompt     on running    → "Session is already running"
  POST /prompt     on terminated → "Session has been terminated"
  POST /terminate  on terminated → "Session is already terminated"
  DELETE /delete   on running    → "Cannot delete a running session"
  POST /sessions   with archived agent/env → "Cannot create session with archived ..."
```

Rules:

- `completed`: runtime exited 0.
- `failed`: runtime exited non-zero, OR an unhandled exception occurred.
  `exit_code` is the process's code (may be `null` for the exception case).
- `terminated`: set by `POST /sessions/{id}/terminate`. Sprite deleted
  best-effort.
- After `completed` or `failed`, you can `POST /sessions/{id}/prompt` to
  continue (turn 2+). After `terminated`, you cannot.

### 6.1 Create a session

```
POST /sessions
```

Body fields:

| Field            | Type             | Required | Default | Notes |
|------------------|------------------|----------|---------|-------|
| `agent_id`       | UUID string      | yes      | —       | Must exist, belong to you, not archived |
| `prompt`         | string           | yes      | —       | Initial user prompt |
| `environment_id` | UUID string \| null | no    | agent's default | Overrides agent's `environment_id` |
| `timeout`        | int (10..3600)   | no       | 600     | Seconds of wall-clock for agent execution |
| `resources`      | array (≤10)      | no       | `[]`    | See §6.2 |

Response: **202** (not 201 — execution is async).

```json
{
  "id": "<session-uuid>",
  "status": "pending",
  "stream_url": "/sessions/<session-uuid>/stream",
  "environment_id": "<uuid>",
  "resources": [...]
}
```

### 6.2 Resources (git repositories)

Each entry mounts a GitHub repo into the Sprite at a specific path before the
agent starts:

```json
{
  "type": "github_repository",
  "url": "https://github.com/org/repo",
  "mount_path": "/workspace/repo",
  "authorization_token": "ghp_xxx"
}
```

- `mount_path` is optional; defaults to `/workspace/<repo-name>`.
- `mount_path` must be absolute, cannot be `/`, cannot be `/home/sprite`.
- Max 10 per session, no duplicate resolved mount_paths.
- `authorization_token` is used for private repos (standard HTTPS basic auth).

### 6.3 GET a session

```
GET /sessions/{id}
```

```json
{
  "id": "<uuid>",
  "agent_id": "<uuid>",
  "environment_id": "<uuid> | null",
  "runtime": "claude",
  "status": "pending | running | completed | failed | terminated",
  "exit_code": 0,
  "created_at": "...",
  "updated_at": "...",
  "resources": [...]
}
```

No `prompt`, no `version`, no `type`, no `archived_at`. `exit_code` is `null`
until the session reaches a terminal state.

### 6.4 SSE stream

```
GET /sessions/{id}/stream
Accept: text/event-stream   (not required)
```

Response headers include `Content-Type: text/event-stream`,
`X-Accel-Buffering: no`. The stream emits these event types as
`data: <json>\n\n`:

| Type         | Payload                                          | When |
|--------------|--------------------------------------------------|------|
| `start`      | `{"type":"start","runtime":"claude","session_id":"<uuid>"}` | Always first, before replay |
| `output`     | `{"type":"output","stream":"stdout"\|"stderr","data":"..."}` | Each line of runtime output |
| `exit`       | `{"type":"exit","code":0}`                       | Terminal. Normal completion or non-zero failure |
| `error`      | `{"type":"error","message":"..."}`               | Terminal. Failure without an exit code (unhandled exception) |
| `terminated` | `{"type":"terminated","message":"Session terminated"}` | Terminal. After `POST /terminate` |

Heartbeats appear every 15 seconds as lines starting with `: ` (note the
leading colon). **Skip any line that starts with `:`.**

**Replay behavior**: connecting to a stream always replays ALL stored output
from the beginning. If the session is already terminal, you'll get `start` →
every buffered `output` event → terminal event, then the stream closes.
Connecting mid-session replays everything so far, then tails live output.

Python reference client:

```python
import json, requests

resp = requests.get(
    f"{BASE}/sessions/{session_id}/stream",
    headers={"Authorization": f"Bearer {TOKEN}"},
    stream=True,
)
resp.raise_for_status()

for line in resp.iter_lines(decode_unicode=True):
    if not line or line.startswith(":"):
        continue  # blank line between events, or heartbeat
    if not line.startswith("data: "):
        continue  # defensive
    event = json.loads(line[6:])
    if event["type"] == "output":
        print(event["data"], end="")
    elif event["type"] in ("exit", "error", "terminated"):
        print(f"\n[{event['type']}] {event.get('code', event.get('message', ''))}")
        break
```

### 6.5 Multi-turn via POST /prompt

```
POST /sessions/{id}/prompt
{"prompt": "follow-up question", "timeout": 600}
```

Allowed when status is `pending`, `completed`, or `failed`. Returns **202**
with the same `id` and `stream_url`. State resets to `pending` and execution
resumes in the same Sprite container.

What persists between turns:

- Sprite filesystem (files you wrote in turn 1 are still there).
- Runtime session history (including the agent's original system prompt).
- Installed packages, exported env vars, cloned repos.
- MCP server configuration.

What does NOT re-run between turns:

- Agent `system` is NOT re-prepended.
- Environment `setup_script` is NOT re-run.
- Packages are NOT re-installed.
- Env vars are NOT re-exported (but are still in-process from turn 1).
- MCP servers are NOT re-configured.

Conflict cases: prompt on `running` → 409; prompt on `terminated` → 409.

### 6.6 Terminate and delete

```
POST /sessions/{id}/terminate
```

Allowed from any non-terminated state. Best-effort deletes the Sprite
(errors swallowed), sets `status="terminated"`. Record is kept — you can
still `GET` it and stream its replay. Second terminate → 409.

```
DELETE /sessions/{id}/delete
```

Hard-deletes the session record (and its logs). Blocked if currently
`running` → 409. All other states are deletable — you do **not** need to
terminate first. Response: `200 {"detail":"Session deleted"}`. Subsequent
`GET` → 404.

---

## 7. End-to-end worked example

Goal: create an agent + environment, run a prompt, continue with a second
prompt, terminate, clean up.

```bash
BASE=http://localhost:8777
TOKEN=fairy_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# 1. Create an environment with a pip package and limited networking.
ENV_ID=$(curl -s -X POST "$BASE/environments" -H "$AUTH" -H "$JSON" -d '{
  "name": "demo",
  "packages": {"pip": ["requests"]},
  "env_vars": {"DEMO": "1"},
  "networking": {"type": "limited", "allowed_hosts": ["pypi.org", "files.pythonhosted.org"]}
}' | jq -r .id)

# 2. Create an agent that uses that environment by default.
AGENT_ID=$(curl -s -X POST "$BASE/agents" -H "$AUTH" -H "$JSON" -d "{
  \"name\": \"demo-agent\",
  \"model\": \"claude-sonnet-4-6\",
  \"runtime\": \"claude\",
  \"system\": \"You are a terse assistant.\",
  \"environment_id\": \"$ENV_ID\"
}" | jq -r .id)

# 3. Start a session (turn 1).
SESS_ID=$(curl -s -X POST "$BASE/sessions" -H "$AUTH" -H "$JSON" -d "{
  \"agent_id\": \"$AGENT_ID\",
  \"prompt\": \"Create /tmp/marker.txt with the content HELLO.\",
  \"timeout\": 120
}" | jq -r .id)

# 4. Stream until exit. (curl -N disables buffering.)
curl -N -H "$AUTH" "$BASE/sessions/$SESS_ID/stream"
# ...
# data: {"type":"start","runtime":"claude","session_id":"..."}
# data: {"type":"output","stream":"stdout","data":"..."}
# data: {"type":"exit","code":0}

# 5. Verify terminal state.
curl -s -H "$AUTH" "$BASE/sessions/$SESS_ID" | jq .status
# "completed"

# 6. Follow-up turn in the same Sprite.
curl -X POST "$BASE/sessions/$SESS_ID/prompt" -H "$AUTH" -H "$JSON" -d '{
  "prompt": "Read /tmp/marker.txt and print its contents.",
  "timeout": 60
}'

# 7. Consume turn-2 stream. It replays turn-1 output first, then tails turn-2.
curl -N -H "$AUTH" "$BASE/sessions/$SESS_ID/stream"

# 8. Terminate (not strictly required if completed, but frees the Sprite sooner).
curl -X POST -H "$AUTH" "$BASE/sessions/$SESS_ID/terminate"
# → 200 {"id":"...","status":"terminated"}

# 9. Delete the session record.
curl -X DELETE -H "$AUTH" "$BASE/sessions/$SESS_ID/delete"
# → 200 {"detail":"Session deleted"}

# 10. Clean up agent + environment (archive — delete the env would 409 since
#     a session referenced it, even though we deleted the session record).
curl -X POST -H "$AUTH" "$BASE/agents/$AGENT_ID/archive"
curl -X POST -H "$AUTH" "$BASE/environments/$ENV_ID/archive"
```

---

## 8. Error code reference

| Code | `detail` type | Meaning                                                                 | Example body                                                        |
|------|---------------|-------------------------------------------------------------------------|---------------------------------------------------------------------|
| 200  | string / object | OK, or successful delete/terminate                                     | `{"detail":"Session deleted"}`                                      |
| 201  | object          | Resource created (agents, environments)                                 | `{"id":"...","version":1,...}`                                      |
| 202  | object          | Session accepted, running in background                                 | `{"id":"...","status":"pending","stream_url":"..."}`                |
| 400  | string          | Bad request: invalid JSON, unknown runtime, runtime has no API key      | `{"detail":"Invalid JSON"}`                                         |
| 401  | string          | Missing / invalid / inactive / expired token                            | `{"detail":"Invalid API key"}`                                      |
| 404  | string          | Resource not found (or not owned by this token's user)                  | `{"detail":"Agent not found"}`                                      |
| 405  | string          | Method not allowed                                                      | `{"detail":"Method not allowed"}`                                   |
| 409  | string          | Conflict: stale version, already archived/terminated, running-session delete, etc. | `{"detail":"Version mismatch: expected 3, got 2"}`         |
| 422  | **list**        | Pydantic validation failure — `detail` is an array of error objects     | `{"detail":[{"type":"missing","loc":["prompt"],"msg":"Field required"}]}` |
| 502  | string          | Sprites upstream error (create/policy/exec)                             | `{"detail":"Failed to create Sprite: connection refused"}`          |

Key rule for clients: `isinstance(detail, list)` iff status is 422.

---

## 9. Full route table

```
GET    /health                              # public
POST   /agents                              # create                → 201
GET    /agents                              # list (non-archived)   → 200 {"data":[...]}
GET    /agents/{uuid}                       # retrieve              → 200
PUT    /agents/{uuid}                       # update (version req.) → 200
POST   /agents/{uuid}/archive               # archive               → 200
GET    /agents/{uuid}/versions              # version history       → 200 {"data":[...]}
POST   /environments                        # create                → 201
GET    /environments                        # list (non-archived)   → 200 {"data":[...]}
GET    /environments/{uuid}                 # retrieve              → 200
PUT    /environments/{uuid}                 # update (version req.) → 200
POST   /environments/{uuid}/archive         # archive               → 200
DELETE /environments/{uuid}/delete          # hard delete           → 200
GET    /environments/{uuid}/versions        # version history       → 200 {"data":[...]}
POST   /sessions                            # create                → 202
GET    /sessions/{uuid}                     # retrieve              → 200
POST   /sessions/{uuid}/prompt              # multi-turn resume     → 202
POST   /sessions/{uuid}/terminate           # terminate             → 200
DELETE /sessions/{uuid}/delete              # delete record         → 200
GET    /sessions/{uuid}/stream              # SSE stream            → 200 text/event-stream
```

There is no `GET /sessions` list endpoint. Track session IDs client-side.

---

Last updated: 2026-04-17 (git `26123ac`). Source of truth for any
disagreement: `src/fairy/urls.py`, `src/fairy/views.py`, and
`tests/e2e/*` — the e2e tests drive a real deployment and are the most
reliable executable spec.
