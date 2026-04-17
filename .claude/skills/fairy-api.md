---
name: fairy-api
description: Use when driving the Fairy REST API — creating agents, environments, or sessions; writing/maintaining `tests/e2e/`; adding new endpoints; or debugging 4xx responses. Covers auth, the route table, the `detail`-is-a-list quirk for 422, optimistic concurrency (`version`), agent-metadata-merge vs env_vars-full-replacement divergence, the session state machine with 409 edges, SSE stream consumption, and multi-turn session semantics. Canonical full reference is `docs/API.md`.
---

# Fairy API Skill

Reference for driving the Fairy REST API — three resources (agents, environments, sessions) used to run AI coding agents inside Sprites.

## When This Skill Applies

Use this skill when:
- Calling the Fairy API from code, tests, or curl (creating agents, environments, or sessions)
- Writing or maintaining e2e tests in `tests/e2e/`
- Adding new endpoints — keep the conventions here consistent
- Debugging 4xx responses (especially 409, 422, or the `detail`-is-a-list edge case)

Canonical full-depth reference: `docs/API.md`. This skill is the shorter operator view with the gotchas front-loaded.

## Base URL & Auth

- Dev: `http://localhost:8777` (what `make dev` serves)
- E2E default when invoking through `make`: same `http://localhost:8777`; raw `pytest` defaults to `http://localhost:8000`
- Every endpoint except `GET /health` requires `Authorization: Bearer fairy_<token>`. Tokens are created server-side via `APIKey.create_key(user, name)` (Django shell/management command).

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
- `GET /sessions` and `GET /sessions/{id}` return the same per-session shape (no `prompt`, no `version`, no `archived_at`).

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

`env_vars` can be set on create/update but is always omitted from `_serialize_environment`. To verify a value, check it from inside a running session (`echo $VAR`) — you cannot GET it back from the API.

### 3. Session create is 202, not 201

Execution starts in a background thread. The caller must consume the stream (or poll GET) to observe completion. Don't assume a session with status `pending` has done anything yet.

### 4. Multi-turn does NOT re-apply setup

On `POST /sessions/{id}/prompt`:
- The agent's `system` prompt is **not** re-prepended (the runtime session history already has it from turn 1).
- Environment `setup_script`, `packages`, `env_vars`, MCP servers — **none** re-run/re-apply on turn 2+.
- The Sprite filesystem persists between turns.

If you need new packages or env vars mid-conversation, start a new session.

### 5. 409 edges on sessions

- `POST /prompt` on `running` → `"Session is already running"`
- `POST /prompt` on `terminated` → `"Session has been terminated"`
- `POST /terminate` on `terminated` → `"Session is already terminated"`
- `DELETE /delete` on `running` → `"Cannot delete a running session"`
- `POST /sessions` with archived agent/env → `"Cannot create session with archived ..."`

Terminate is idempotent-error (409), not idempotent-OK.

### 6. Runtime/model pairing is NOT cross-validated

You can save `runtime="gemini"` with `model="claude-opus-4-6"` — the API will 201. The session will fail at exec time with a less obvious error. Always pair correctly from the matrix below.

### 7. Archive vs delete on environments

- Archive (`POST /environments/{id}/archive`) → soft, reversible-ish (no un-archive endpoint), but rows stay. Second archive → 409.
- Delete (`DELETE /environments/{id}/delete`) → hard, cascades versions. **Blocked by 409 if any session — even terminated ones — references this environment.** Does not require prior archive.
- Practical pattern: **prefer archive**. The e2e fixtures use archive for cleanup to avoid the sessions-exist 409.

### 8. Agents cannot disable individual tools

Each runtime runs with its full default tool set (bash/read/write/edit/glob/grep/web_fetch/web_search). MCP servers on the agent are additive. There is no tool allowlist or per-tool disable switch.

## Runtime & Model Matrix

| Runtime        | Valid models                                                                              |
| -------------- | ----------------------------------------------------------------------------------------- |
| `claude`       | `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`, plus pinned variants in `runtimes.py` |
| `claude-oauth` | same as `claude` (OAuth auth path)                                                        |
| `codex`        | `gpt-4.1`, `o3`, `o4-mini`                                                                |
| `gemini`       | `gemini-2.5-pro`, `gemini-2.5-flash`                                                      |

Source of truth: `src/fairy/runtimes.py` — `AgentModel` + `RUNTIMES` + `MODEL_RUNTIME_MAP`.

If you see `400 {"detail":"No API key configured for runtime: <name>"}` at session create, the user hasn't added a `UserRuntimeKey` for that runtime.

## Session State Machine

```
                   POST /sessions
                        │
                        ▼
                   ┌─────────┐
                   │ pending │◄────────────────────────────────┐
                   └────┬────┘                                 │
                        │ background execution                 │ POST /sessions/{id}/prompt
                        ▼                                      │ (allowed on pending/completed/failed)
                   ┌─────────┐                                 │
                   │ running │─────────────────────────────────┘
                   └────┬────┘
          ┌─────────────┼──────────────┐
          ▼             ▼              ▼
    ┌──────────┐  ┌────────┐  ┌──────────────┐
    │completed │  │ failed │  │  terminated  │◄── POST /sessions/{id}/terminate
    └──────────┘  └────────┘  └──────────────┘
```

- `completed`: exit_code == 0
- `failed`: non-zero exit OR unhandled exception (exit_code may be null)
- `terminated`: explicit terminate; Sprite deleted best-effort; record kept

## SSE Stream

`GET /sessions/{id}/stream` — `Content-Type: text/event-stream`, `X-Accel-Buffering: no`.

Event types (`data: <json>\n\n`):

| Type         | Payload                                                            |
| ------------ | ------------------------------------------------------------------ |
| `start`      | `{"type":"start","runtime":"claude","session_id":"<uuid>"}` (always first) |
| `output`     | `{"type":"output","stream":"stdout"\|"stderr","data":"..."}`       |
| `exit`       | `{"type":"exit","code":0}` — terminal                              |
| `error`      | `{"type":"error","message":"..."}` — terminal (exception path)     |
| `terminated` | `{"type":"terminated","message":"Session terminated"}` — terminal  |

Heartbeats are lines starting with `: ` (skip them). Stream always replays **everything** from the start on every connection — then tails live output if still running, or emits terminal event + closes if already terminal. Reconnects are safe and idempotent.

Reference client:

```python
import json, requests

resp = requests.get(
    f"{BASE}/sessions/{sid}/stream",
    headers={"Authorization": f"Bearer {TOKEN}"},
    stream=True,
)
resp.raise_for_status()
for line in resp.iter_lines(decode_unicode=True):
    if not line or line.startswith(":"):
        continue
    event = json.loads(line[len("data: "):])
    if event["type"] == "output":
        print(event["data"], end="")
    elif event["type"] in ("exit", "error", "terminated"):
        break
```

## Minimum Viable curl Recipes

```bash
BASE=http://localhost:8777
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# Create agent
curl -X POST "$BASE/agents" -H "$AUTH" -H "$JSON" \
  -d '{"name":"demo","model":"claude-sonnet-4-6","runtime":"claude","system":"You are terse."}'

# Update agent (stale version → 409)
curl -X PUT "$BASE/agents/<id>" -H "$AUTH" -H "$JSON" \
  -d '{"version":1,"metadata":{"key-to-delete":"","keep":"val"}}'

# Create environment with limited networking
curl -X POST "$BASE/environments" -H "$AUTH" -H "$JSON" -d '{
  "name":"demo",
  "packages":{"pip":["requests"]},
  "env_vars":{"DEMO":"1"},
  "networking":{"type":"limited","allowed_hosts":["pypi.org","files.pythonhosted.org"]}
}'

# Create session (202)
curl -X POST "$BASE/sessions" -H "$AUTH" -H "$JSON" \
  -d '{"agent_id":"<id>","prompt":"hello","timeout":120}'

# Stream until exit
curl -N -H "$AUTH" "$BASE/sessions/<id>/stream"

# Multi-turn (reuses same session row + Sprite)
curl -X POST "$BASE/sessions/<id>/prompt" -H "$AUTH" -H "$JSON" \
  -d '{"prompt":"follow up"}'

# Terminate (best-effort Sprite delete, record kept)
curl -X POST -H "$AUTH" "$BASE/sessions/<id>/terminate"

# Delete record (blocked if running)
curl -X DELETE -H "$AUTH" "$BASE/sessions/<id>/delete"
```

## Error Code Reference

| Code | `detail` type | Common causes                                                                 |
| ---- | ------------- | ----------------------------------------------------------------------------- |
| 400  | string        | Invalid JSON, unknown runtime, no runtime API key configured                  |
| 401  | string        | Missing/invalid/inactive/expired bearer token                                 |
| 404  | string        | Resource not found or not owned by this token's user                          |
| 405  | string        | Method not allowed on that route                                              |
| 409  | string        | Version mismatch, already archived/terminated, running-session delete, etc.   |
| 422  | **list**      | Pydantic validation failure — `detail` is an array of error dicts             |
| 502  | string        | Sprites upstream error (create/policy/exec)                                   |

## Related Files

- `src/fairy/urls.py` — authoritative route table
- `src/fairy/views.py` — request models, validation, serializers, all endpoints
- `src/fairy/models.py` — `Agent`, `Environment`, `AgentSession`, version history, `APIKey`
- `src/fairy/runtimes.py` — `AgentModel`, `RUNTIMES`, `MODEL_RUNTIME_MAP`
- `src/fairy/stream.py` — background runner + SSE event emission
- `src/fairy/sprites_exec.py` — `build_wrapper_script` (sets order of env → packages → clone → setup → exec)
- `tests/e2e/conftest.py` — canonical fixtures (`create_agent`, `create_environment`, `create_session`)
- `docs/API.md` — full reference with worked end-to-end examples
- `thoughts/research/2026-04-17-fairy-api-docs.md` — research synthesis behind this skill
