---
name: agent-on-demand-api
description: Use when driving the Agent on Demand REST API — creating agents, environments, or sessions; writing/maintaining `tests/e2e/`; adding new endpoints; or debugging 4xx responses. Covers auth, the route table, the `detail`-is-a-list quirk for 422, optimistic concurrency (`version`), agent-metadata-merge vs env_vars-full-replacement divergence, the session state machine (failed is terminal), session resources (GitHub repo clone), concurrent-session quota (429), SSE stream with stage events, and multi-turn session semantics. Canonical spec is `docs/openapi.yaml`.
---

# Agent on Demand API Skill

Reference for driving the Agent on Demand REST API — three resources (agents, environments, sessions) used to run AI coding agents inside Sprites.

## When This Skill Applies

Use this skill when:
- Calling the Agent on Demand API from code, tests, or curl (creating agents, environments, or sessions)
- Writing or maintaining e2e tests in `tests/e2e/`
- Adding new endpoints — keep the conventions here consistent
- Debugging 4xx responses (especially 409, 422, 429, or the `detail`-is-a-list edge case)

Canonical spec: `docs/openapi.yaml`. The operator site is rendered at `https://ravi-hq.github.io/agent-on-demand`. This skill is the shorter operator view with the gotchas front-loaded.

## Base URL & Auth

- Dev: `http://localhost:8777` (what `make dev` serves)
- E2E default when invoking through `make`: same `http://localhost:8777`; raw `pytest` defaults to `http://localhost:8000`
- Every endpoint except `GET /health` requires `Authorization: Bearer aod_<token>`. Tokens are created server-side via `APIKey.create_key(user, name)` (Django shell/management command).
- Runtime auth (provider keys, OAuth tokens) is a separate concept — stored as `UserCredential` rows keyed by `kind` (e.g. `provider:anthropic`, `runtime_token:claude-oauth`). Missing credentials for a session's runtime → `400 "No API key configured for runtime: <name>"`.

## Route Table

```
GET    /health                              # public
POST   /agents                              # 201
GET    /agents                              # 200 {"data":[...]}  (non-archived)
GET    /agents/{uuid}                       # 200
PUT    /agents/{uuid}                       # 200 (version required)
POST   /agents/{uuid}/archive               # 200
GET    /agents/{uuid}/versions              # 200 {"data":[...]}
POST   /environments                        # 201
GET    /environments                        # 200 {"data":[...]}  (non-archived)
GET    /environments/{uuid}                 # 200
PUT    /environments/{uuid}                 # 200 (version required)
POST   /environments/{uuid}/archive         # 200
DELETE /environments/{uuid}/delete          # 200 (hard delete; blocked if sessions exist)
GET    /environments/{uuid}/versions        # 200 {"data":[...]}
POST   /sessions                            # 202  ← not 201; execution is async
GET    /sessions                            # 200 {"data":[...]}  (all statuses, newest first)
GET    /sessions/{uuid}                     # 200
POST   /sessions/{uuid}/prompt              # 202  (multi-turn)
GET    /sessions/{uuid}/turns               # 200 {"data":[...]}  turn history
POST   /sessions/{uuid}/terminate           # 200
DELETE /sessions/{uuid}/delete              # 200
GET    /sessions/{uuid}/stream              # 200 text/event-stream
```

`GET /sessions` returns every session the caller owns (all statuses — no archive concept), newest first. `GET /agents` and `GET /environments` return the non-archived set. None of the list endpoints take query params or paginate.

## Conventions

### Response shapes

- List: `{"data":[<resource>,...]}` — no pagination, no query params.
- Single: resource object, no envelope.
- Error: `{"detail": ...}` for every status, including successful deletes (`{"detail":"Session deleted"}`).
- `GET /sessions` and `GET /sessions/{id}` return the same per-session shape: `id`, `agent_id`, `environment_id`, `runtime`, `status`, `exit_code`, `created_at`, `updated_at`, `resources`, `turn_count`, `current_turn`. No `prompt`, no `version`, no `archived_at`.
- `POST /sessions` and `POST /sessions/{id}/prompt` return a trimmed ack (`id`, `status`, `stream_url`, `current_turn`, plus `environment_id`/`resources` on create). To get the full session, `GET /sessions/{id}` after.

### The 422 quirk

For every status code except 422, `detail` is a **string**. For 422 (Pydantic validation failure), `detail` is a **list** of error objects:

```json
{"detail":[{"type":"missing","loc":["prompt"],"msg":"Field required","input":{}}]}
```

Any client that parses errors needs `isinstance(detail, list)` handling.

### IDs and timestamps

- IDs: UUID v4, lowercase, server-assigned. Clients never supply IDs.
- Timestamps: ISO 8601 with UTC offset. Fields used: `created_at`, `updated_at`, `archived_at` (nullable).

### Optimistic concurrency (agents & environments only)

Every PUT requires `{"version": N}` matching current state. Stale → `409 {"detail":"Version mismatch: expected N, got M"}`. No-op PUTs don't bump the version. Sessions have no version.

## Critical Gotchas

### 1. Agent metadata merge vs environment env_vars replacement

The two resources handle bag-of-key-values fields **differently**:

- **Agent `metadata`**: per-key merge. Empty string deletes that key. Omitted keys are unchanged.
  - Current `{"team":"platform","env":"prod"}` + PUT `{"metadata":{"env":"staging","team":""}}` → `{"env":"staging"}`
- **Environment `env_vars`**: **full replacement**. Any key not in the payload is removed.
  - Current `{"A":"1","B":"2"}` + PUT `{"env_vars":{"B":"2","C":"3"}}` → `{"B":"2","C":"3"}` (A is gone)
  - To add without deleting, re-send every key you want to keep.

### 2. env_vars is never returned in responses

`env_vars` can be set on create/update but is always omitted from `_serialize_environment` (also encrypted at rest). To verify a value, check it from inside a running session (`echo $VAR`) — you cannot GET it back from the API.

### 3. Session create is 202, not 201

Execution is enqueued as a Procrastinate task and runs in the worker process. The caller must consume the stream (or poll `GET /sessions/{id}`) to observe completion. Don't assume a session with status `pending` has done anything yet. The 202 ack carries `stream_url` and `current_turn` — use those instead of guessing paths.

### 4. Multi-turn does NOT re-apply setup; `failed` is terminal

On `POST /sessions/{id}/prompt`:
- The agent's `system` prompt is **not** re-prepended (the runtime CLI's own `--continue`/`--resume` carries conversation state from turn 1).
- Environment `setup_script`, `packages`, `env_vars`, MCP servers, skills — **none** re-run/re-apply on turn 2+.
- The Sprite filesystem persists between turns.
- Allowed only on `pending` or `completed`. Both `running` (409 "already running") **and `failed` (409 "has failed and cannot be resumed")** are blocked. `failed` used to be resumable — it isn't anymore; a failed turn may have left the Sprite in a bad state, so start a new session instead.

If you need new packages or env vars mid-conversation, start a new session.

### 5. 409 edges on sessions

- `POST /prompt` on `running` → `"Session is already running"`
- `POST /prompt` on `failed` → `"Session has failed and cannot be resumed. Start a new session."`
- `POST /prompt` on `terminated` → `"Session has been terminated"`
- `POST /terminate` on `terminated` → `"Session is already terminated"`
- `DELETE /delete` on `running` → `"Cannot delete a running session"`
- `POST /sessions` with archived agent/env → `"Cannot create session with archived ..."`

Terminate is idempotent-error (409), not idempotent-OK.

### 6. Runtime/model pairing IS cross-validated

The API enforces that the agent's `model` is servable by the agent's `runtime` (provider of the model must be in the runtime's `providers` set). Enforced on `POST /agents`, `PUT /agents/{id}`, and again at session create — returns **422** with `"Runtime X cannot serve model Y: provider Z not in [...]"`. Pair correctly from the matrix below; the validator will reject mismatches before a Sprite is created.

### 7. Archive vs delete on environments

- Archive (`POST /environments/{id}/archive`) → soft, reversible-ish (no un-archive endpoint), rows stay. Second archive → 409.
- Delete (`DELETE /environments/{id}/delete`) → hard, cascades versions. **Blocked by 409 if any session — even terminated ones — references this environment.** Does not require prior archive.
- Practical pattern: **prefer archive**. The e2e fixtures use archive for cleanup to avoid the sessions-exist 409.

### 8. Agents cannot disable individual tools

Each runtime runs with its full default tool set (bash/read/write/edit/glob/grep/web_fetch/web_search). MCP servers and skills on the agent are additive. There is no tool allowlist or per-tool disable switch.

### 9. Concurrent-session quota returns 429

`POST /sessions` counts the caller's `pending` + `running` sessions against their quota (`UserQuota.max_concurrent_sessions`, default from `settings.DEFAULT_MAX_CONCURRENT_SESSIONS`). Exceeded → `429` with `{"detail": "...", "limit": N, "active": M}` — the only endpoint that surfaces extra keys alongside `detail`.

### 10. Session `resources` (GitHub repos)

`POST /sessions` accepts up to 10 `resources[]` entries of `{"type":"github_repository","url":"https://github.com/<owner>/<repo>"[,"mount_path":"/absolute/path"][,"authorization_token":"<PAT>"]}`. The repo is cloned inside the Sprite during provisioning. `mount_path` defaults to `/workspace/<repo-name>`, must be absolute, cannot be `/` or `/home/sprite`, and must be unique across the request. `authorization_token` (for private repos) is encrypted at rest and **never echoed back** on any response.

## Runtime & Model Matrix

Model IDs are canonical `provider/model_id` strings. Agent create/update rejects any ID not in `MODELS`.

| Runtime    | Providers                       | Valid models                                                                                                                                                                        |
| ---------- | ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `claude`   | `anthropic`                     | `anthropic/claude-opus-4-6`, `anthropic/claude-sonnet-4-6`, `anthropic/claude-haiku-4-5`, plus pinned variants (`claude-opus-4-0-20250514`, `claude-sonnet-4-0-20250514`, `claude-sonnet-4-5-20250514`, `claude-3-5-haiku-20241022`) |
| `codex`    | `openai`                        | `openai/gpt-4.1`, `openai/o3`, `openai/o4-mini`                                                                                                                                     |
| `gemini`   | `google`                        | `google/gemini-2.5-pro`, `google/gemini-2.5-flash`                                                                                                                                  |
| `opencode` | `anthropic`, `openai`, `google` | any of the above (meta-runtime; picks provider+model per invocation via `--model`)                                                                                                  |

Source of truth: `src/agent_on_demand/models_catalog.py` (`MODELS`) and `src/agent_on_demand/runtimes/` (per-runtime `Runtime.providers`).

Claude accepts either a `provider:anthropic` credential (ANTHROPIC_API_KEY) or a `runtime_token:claude-oauth` credential (CLAUDE_CODE_OAUTH_TOKEN) — both authenticate the same `claude` runtime. There is no separate `claude-oauth` runtime anymore.

If you see `400 {"detail":"No API key configured for runtime: <name>"}` at session create, the user hasn't registered a `UserCredential` of an accepted `kind` for that runtime.

## Session State Machine

```
                   POST /sessions
                        │
                        ▼
                   ┌─────────┐
                   │ pending │◄────────────────────────────────┐
                   └────┬────┘                                 │
                        │ worker picks up task                 │ POST /sessions/{id}/prompt
                        ▼                                      │ (allowed only on pending/completed)
                   ┌─────────┐                                 │
                   │ running │─────────────────────────────────┘
                   └────┬────┘
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
    ┌──────────┐  ┌────────┐  ┌──────────────┐
    │completed │  │ failed │  │  terminated  │◄── POST /sessions/{id}/terminate
    └──────────┘  └────────┘  └──────────────┘
                      │
                      └─ terminal: /prompt returns 409. Start a new session.
```

- `completed`: exit_code == 0
- `failed`: non-zero exit OR unhandled exception (exit_code may be null). Terminal — no resume.
- `terminated`: explicit terminate; Sprite deleted best-effort; record kept

## SSE Stream

`GET /sessions/{id}/stream` — `Content-Type: text/event-stream`, `X-Accel-Buffering: no`.

Event types (`data: <json>\n\n`). Every event except `start` includes an `"id": <log_row_id>` field in the JSON payload and an matching SSE `id:` line.

| Type         | Payload                                                                                                    |
| ------------ | ---------------------------------------------------------------------------------------------------------- |
| `start`      | `{"type":"start","runtime":"claude","session_id":"<uuid>"}` (always first, no `id`)                        |
| `stage`      | `{"type":"stage","id":<int>,"stage":"<name>","state":"started"\|"completed"\|"failed"[,"duration_ms":N][,"message":"..."]}` — provisioning progress |
| `turn_start` | `{"type":"turn_start","id":<int>,"turn":<int>}` — before first output of each turn                         |
| `output`     | `{"type":"output","id":<int>,"stream":"stdout"\|"stderr","data":"...","turn":<int>}`                       |
| `exit`       | `{"type":"exit","id":<int>,"code":0}` — terminal                                                           |
| `error`      | `{"type":"error","id":<int>,"message":"..."}` — terminal (exception path; `failed` with no exit_code)      |
| `terminated` | `{"type":"terminated","id":<int>,"message":"Session terminated"}` — terminal                              |
| `stale`      | `{"type":"stale","id":<int>,"message":"No output for 600s"}` — terminal; session may still be `running`   |

Stage names (emitted in order during provisioning): `create_sprite`, `install_runtime`, `network_policy`, `env_file`, `git_credentials`, `provision_setup`, `runtime_config`, `skills`, `runtime_start`.

Heartbeats are lines starting with `: ` (skip them). Stream replays everything from the start by default; supply the last received `id` via `Last-Event-ID` header or `?since=<id>` query param to resume without re-receiving old events. If both are supplied, the header wins. `since=0` or omitting it gives a full replay. Non-integer `since` returns `400`.

Reference client:

```python
import json, requests

last_event_id = 0
while True:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if last_event_id:
        headers["Last-Event-ID"] = str(last_event_id)
    with requests.get(f"{BASE}/sessions/{sid}/stream", headers=headers, stream=True) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line or line.startswith(":"):
                continue
            if line.startswith("id: "):
                last_event_id = int(line[4:])
            elif line.startswith("data: "):
                event = json.loads(line[6:])
                if event["type"] == "output":
                    print(event["data"], end="")
                elif event["type"] in ("exit", "error", "terminated", "stale"):
                    return
```

## Minimum Viable curl Recipes

```bash
BASE=http://localhost:8777
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# Create agent (note provider/model_id canonical form)
curl -X POST "$BASE/agents" -H "$AUTH" -H "$JSON" \
  -d '{"name":"demo","model":"anthropic/claude-sonnet-4-6","runtime":"claude","system":"You are terse."}'

# Update agent (stale version → 409; incompatible runtime/model → 422)
curl -X PUT "$BASE/agents/<id>" -H "$AUTH" -H "$JSON" \
  -d '{"version":1,"metadata":{"key-to-delete":"","keep":"val"}}'

# Create environment with limited networking
curl -X POST "$BASE/environments" -H "$AUTH" -H "$JSON" -d '{
  "name":"demo",
  "packages":{"pip":["requests"]},
  "env_vars":{"DEMO":"1"},
  "networking":{"type":"limited","allowed_hosts":["pypi.org","files.pythonhosted.org"]}
}'

# Create session (202) — optionally with GitHub repo resources
curl -X POST "$BASE/sessions" -H "$AUTH" -H "$JSON" -d '{
  "agent_id":"<id>",
  "prompt":"summarize the README",
  "timeout":120,
  "resources":[{"type":"github_repository","url":"https://github.com/org/repo"}]
}'

# Stream until exit
curl -N -H "$AUTH" "$BASE/sessions/<id>/stream"

# Multi-turn (only valid on pending/completed; 409 on running/failed/terminated)
curl -X POST "$BASE/sessions/<id>/prompt" -H "$AUTH" -H "$JSON" \
  -d '{"prompt":"follow up"}'

# List turn history
curl -H "$AUTH" "$BASE/sessions/<id>/turns"

# Terminate (best-effort Sprite delete, record kept)
curl -X POST -H "$AUTH" "$BASE/sessions/<id>/terminate"

# Delete record (blocked if running)
curl -X DELETE -H "$AUTH" "$BASE/sessions/<id>/delete"
```

## Error Code Reference

| Code | `detail` type | Common causes                                                                                      |
| ---- | ------------- | -------------------------------------------------------------------------------------------------- |
| 400  | string        | Invalid JSON, unknown runtime, no Sprites key, no runtime credential configured, `since` not int   |
| 401  | string        | Missing/invalid/inactive/expired bearer token                                                      |
| 404  | string        | Resource not found or not owned by this token's user                                               |
| 405  | string        | Method not allowed on that route                                                                   |
| 409  | string        | Version mismatch, archived-already, terminated-already, running-session delete, failed-resume, etc.|
| 422  | **list**      | Pydantic validation failure — `detail` is an array of error dicts. Also used for runtime/model incompat. |
| 429  | string        | Per-user concurrent-session quota exceeded. Body also carries numeric `limit` and `active` keys.   |
| 502  | string        | Sprites upstream error (create/policy/exec)                                                        |

## Related Files

- `src/agent_on_demand/urls.py` — authoritative route table
- `src/agent_on_demand/views/` — request models, validation, serializers, per-resource endpoints
- `src/agent_on_demand/models/` — `Agent`, `Environment`, `AgentSession`, `SessionTurn`, `SessionResource`, version history, `APIKey`, `UserCredential`, `UserQuota`
- `src/agent_on_demand/models_catalog.py` — `MODELS` (canonical `provider/model_id` catalog)
- `src/agent_on_demand/runtimes/` — per-runtime `Runtime` classes (`claude.py`, `codex.py`, `gemini.py`, `opencode.py`) and the `RUNTIMES` registry
- `src/agent_on_demand/stream.py` — SSE replay generator (tails `AgentSessionLog`)
- `src/agent_on_demand/session_service/` — Sprites orchestration (`provisioning.py`, `tasks.py`, `turn.py`)
- `tests/e2e/conftest.py` — canonical fixtures (`create_agent`, `create_environment`, `create_session`) and the `AgentOnDemandAPI` test client
- `docs/openapi.yaml` — full OpenAPI 3.1 spec (canonical machine-readable reference)
- `thoughts/research/2026-04-17-fairy-api-docs.md` — research synthesis behind this skill
