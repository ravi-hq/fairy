---
date: 2026-04-17T14:58:00+00:00
researcher: Claude Code (team-research skill)
git_commit: 26123acbb1f6c65f312350cfda8b86cb1fc4b9f2
branch: add-mcp-e2e-tests
repository: ravi-hq/fairy
topic: "How to use the Fairy API end-to-end — agents, environments, sessions — for a Claude Code agent audience"
tags: [research, team-research, api, documentation, agents, environments, sessions, auth]
status: complete
method: agent-team
team_size: 4
tracks: [agents-lifecycle, environments-lifecycle, sessions-lifecycle, conventions-and-auth]
last_updated: 2026-04-17
last_updated_by: Claude Code
---

# Research: Fairy API Usage Documentation for Claude Code Agents

**Date**: 2026-04-17T14:58:00+00:00
**Researcher**: Claude Code (team-research)
**Git Commit**: `26123ac`
**Branch**: `add-mcp-e2e-tests`
**Repository**: ravi-hq/fairy
**Method**: Agent team (4 specialist researchers)

## Research Question

Produce plain-text documentation that lets a Claude Code agent drive the Fairy
API end-to-end: set up agents, create environments, and manage the lifecycle of
a session.

## Summary

The Fairy API is a Django REST service at `:8777` (dev) with three resources —
agents, environments, sessions — authenticated via `Authorization: Bearer
fairy_<token>`. All error responses share a single `{"detail": ...}` envelope
(string for most codes, **list** for Pydantic 422). Agents and environments use
optimistic concurrency (`version` on PUT, snapshot history via `/versions`,
`409` on stale version). Sessions have a strict state machine (`pending →
running → {completed, failed, terminated}`), expose a Server-Sent Events
stream that replays from the start on reconnect, and support multi-turn via
`POST /sessions/{id}/prompt` (reuses the same Sprite container). No list
pagination exists; `GET /sessions` does not exist at all. Deliverable: a single
`docs/API.md` file organized by resource lifecycle, plus a shared conventions
section.

## Research Tracks

### Track 1: Agents API lifecycle
**Researcher**: agents-researcher
**Scope**: `src/fairy/views.py`, `models.py`, `runtimes.py`, `urls.py`, `tests/test_agents.py`, `tests/test_runtimes.py`, `tests/test_tools_mcp.py`, `tests/e2e/test_agents.py`

#### Findings

1. **Create body** — `POST /agents` validated by `CreateAgentRequest` (`src/fairy/views.py:582-611`). Required: `name` (≤200), `model` (must be a valid `AgentModel`), `runtime` (must key into `RUNTIMES`). Optional: `system` (""), `description` (""), `environment_id` (null), `skills` ([]), `mcp_servers` ([]), `metadata` ({}). Unknown fields silently dropped by Pydantic (`tests/test_tools_mcp.py:210-224`).
2. **Response shape** — `_serialize_agent` (`views.py:649-666`): `id`, `type="agent"`, `name`, `description` (null if empty), `system` (null if empty), `model`, `runtime`, `environment_id`, `skills`, `mcp_servers`, `metadata`, `version`, `created_at`, `updated_at`, `archived_at`. List wraps in `{"data":[...]}`.
3. **No list filtering** — `GET /agents` returns all non-archived agents, `-created_at` order, no query params (`views.py:750-751`).
4. **PUT with `version`** — stale version → `409 {"detail":"Version mismatch: expected N, got M"}` (`views.py:782-786`). No-op update keeps version unchanged, no snapshot written (`views.py:825-828`).
5. **Metadata merge semantics** — per-key: value = non-empty upserts, value = `""` deletes, omitted keys untouched (`views.py:814-823`). Confirmed in `tests/e2e/test_agents.py:168-182`.
6. **Mutable fields via PUT** — `name`, `model`, `runtime`, `system`, `description`, `environment_id`, `skills`, `mcp_servers`, `metadata`. Immutable: `id`, `created_at`, `user`.
7. **Archive is idempotent-error** — second archive → `409 {"detail":"Agent is already archived"}` (`views.py:845-846`). No un-archive endpoint.
8. **Versions endpoint** — `GET /agents/{id}/versions` → `{"data":[...]}` ordered `-version` (`views.py:864-865`).
9. **MCP servers** — array of objects with `name` (unique), `type` ("url" or "stdio"), plus per-type fields (`url`+`headers`, or `command`+`args`+`env`). Max 20 per agent. Validated by `_validate_mcp_servers` (`views.py:557-579`). On multi-turn, MCP config is NOT re-applied (`views.py:409-413`).
10. **Runtime/model validation is independent** — no cross-check. Claude model + `runtime="gemini"` will save without error. `MODEL_RUNTIME_MAP` is informational only (`runtimes.py:32-45`). Invalid model → 422 list; invalid runtime → 400 string.
11. **System prompt baking** — On session create only, `effective_prompt = f"{agent.system}\n\n{req.prompt}"` (`views.py:241-244`). Multi-turn `/prompt` does NOT re-prepend (`views.py:409-413`).

### Track 2: Environments API lifecycle
**Researcher**: env-researcher
**Scope**: `src/fairy/views.py`, `models.py`, `crypto.py`, `urls.py`, `tests/test_environments.py`, `tests/test_networking_wiring.py`, `tests/e2e/test_environments.py`

#### Findings

1. **Create body** — `POST /environments` (`views.py:875-905`). Required: `name` (≤200). Optional: `packages` ({}), `env_vars` ({}), `setup_script` (""), `networking` ({"type":"unrestricted"}).
2. **Response shape** — `_serialize_environment` (`views.py:940-955`): `id`, `type="environment"`, `name`, `packages`, `setup_script` (null if empty), `networking`, `version`, `created_at`, `updated_at`, `archived_at`. **`env_vars` is NEVER returned** (`tests/e2e/test_environments.py:41`).
3. **env_vars storage** — plaintext in `env_vars` JSONField on Environment model (`models.py:153`). Not Fernet-encrypted (unlike `UserRuntimeKey.encrypted_key` and `SessionResource.encrypted_token`). Hidden only at the serializer layer. *Clarification: CLAUDE.md's "encrypted at rest" is aspirational / out of date — the API surface correctly hides `env_vars`, but the model stores plaintext.*
4. **Packages** — `{"<manager>":["pkg",...]}`. Valid managers: `apt`, `cargo`, `gem`, `go`, `npm`, `pip`. Unknown → 422. Version specs in names pass through verbatim (`test_environments.py:162-170`).
5. **Setup script** — single multiline string, run as bash inside the Sprite wrapper. Order (`sprites_exec.py:50`, `sprites_exec.py:260-318`): env vars → packages (apt→cargo→gem→go→npm→pip) → git clones → `setup_script` → agent CLI. Not re-run on multi-turn `/prompt`.
6. **Networking** — `{"type":"unrestricted"}` or `{"type":"limited","allowed_hosts":[...]}`. Wildcards like `*.github.com` accepted verbatim (`test_networking_wiring.py:83`). Stored as two columns: `networking_type` + `networking_config`. At session start, `limited` triggers `sprite.update_network_policy()` with one `allow` rule per host + trailing `deny *` (`views.py:71-82`, `views.py:233-235`). Empty `allowed_hosts` + `limited` = deny-all.
7. **PUT env_vars is FULL REPLACEMENT** — unlike agent metadata, sending `env_vars` replaces the entire dict (`views.py:1069-1072`). Missing keys from the payload ARE deleted.
8. **Archive vs delete** — archive sets `archived_at` (`409` if already archived). Delete is hard-delete, blocked by any session referencing the env (`409 {"detail":"Cannot delete environment with existing sessions"}`, `views.py:1096-1135`). Archive does NOT need to precede delete.
9. **Versions endpoint** — `GET /environments/{id}/versions` same envelope, `-version` order. `env_vars` excluded from version snapshots too.
10. **No list filtering** — `GET /environments` returns all non-archived, `-created_at`, no query params. Name uniqueness is `(user, name)` where `archived_at IS NULL` — archiving frees the name (`models.py:164-172`).

### Track 3: Sessions lifecycle and streaming
**Researcher**: session-researcher
**Scope**: `src/fairy/views.py`, `models.py`, `sprites_exec.py`, `stream.py`, `urls.py`, `tests/test_api.py`, `tests/e2e/test_sessions.py`

#### Findings

1. **Create body** — `POST /sessions` (`views.py:146-164`). Required: `agent_id`, `prompt`. Optional: `environment_id` (overrides agent default), `timeout` (default 600, 10-3600), `resources` (up to 10 github_repository objects).
2. **Resource shape** — `{"type":"github_repository","url":"...","mount_path":"/workspace/...","authorization_token":"..."}`. `mount_path` defaults to `/workspace/<repo-name>`, must be absolute, cannot be `/` or `/home/sprite`. Max 10 per session, no duplicate mount_paths (`views.py:109-143`).
3. **Create response** — `202 {"id","status":"pending","stream_url":"/sessions/{id}/stream","environment_id","resources"}` (`views.py:300-308`).
4. **State machine** — `pending → running → {completed, failed, terminated}` (`models.py:79-85`). `failed` when runtime exits non-zero OR unhandled exception (`stream.py:119-127`). `completed` when exit_code == 0.
5. **GET response** — `id`, `agent_id`, `environment_id`, `runtime`, `status`, `exit_code` (null until terminal), `created_at`, `updated_at`, `resources`. No `type`, `version`, `archived_at`, `prompt` (`views.py:321-331`).
6. **POST /prompt (multi-turn)** — allowed when state is pending, completed, or failed. Blocked on running (`409 "Session is already running"`), blocked on terminated (`409 "Session has been terminated"`). Reuses the same Sprite container via `continue_session=True`. Session row updated, not replaced. Skills re-materialized; MCP + packages + env_vars + setup_script NOT re-applied (`views.py:399-429`).
7. **Terminate** — any non-terminated state succeeds; double-terminate → 409. Best-effort Sprite delete (SpriteError swallowed). Sets status="terminated", clears sprite_name. Row kept (`views.py:442-470`).
8. **Delete** — blocked if running (409). Allowed on all other states including pending/failed (does NOT require prior terminate). Returns `200 {"detail":"Session deleted"}`; subsequent GET is 404 (`views.py:473-489`).
9. **SSE stream** — `GET /sessions/{id}/stream`, `Content-Type: text/event-stream`, `X-Accel-Buffering: no` (`views.py:334-358`, `stream.py:132-175`). Events: `start`, `output` (`{stream:"stdout|stderr", data}`), `exit` (`{code}`), `error` (`{message}`), `terminated` (`{message}`). Heartbeats are lines starting with `: ` — skip them. Stream replays all logs from start on reconnect, then emits terminal event, then closes.
10. **Multi-turn mechanics** — same Sprite container, same session row, same stream URL. Turn-1 filesystem/state persists for turn 2 (confirmed `tests/e2e/test_sessions.py:219-254`).

### Track 4: Auth, conventions, error model, /health
**Researcher**: conventions-researcher
**Scope**: `src/fairy/auth.py`, `views.py`, `urls.py`, `config/settings.py`, tests

#### Findings

1. **Auth header** — `Authorization: Bearer <token>` (`auth.py:19`). Token format `fairy_<32-byte urlsafe>`. Stored as SHA-256 hash. Optional `is_active` and `expires_at`. Created via `APIKey.create_key(user, name)` (shell/management command only).
2. **Auth errors** — all `401`: missing header, invalid key, inactive, expired (`auth.py:22-37`). Never `403`.
3. **Public endpoints** — only `GET /health` (`urls.py:6`).
4. **/health** — `GET /health → 200 {"status":"ok"}`. No auth. POST/PUT → 405.
5. **No pagination** — all list endpoints return the full set, no `limit`/`offset`/`cursor`. List envelope: `{"data":[...]}`. Singles return the bare object.
6. **No list filtering** — `GET /agents`, `GET /environments` have no query params. `GET /sessions` does NOT exist (`urls.py:20-24`).
7. **Optimistic concurrency** — agents + environments only. `{"version":N}` required on PUT. Stale → `409 {"detail":"Version mismatch: expected N, got M"}`.
8. **Metadata is agents-only** — environments have no `metadata` field.
9. **Error envelope** — universal `{"detail": ...}`. `detail` is string for all codes EXCEPT **422 (Pydantic) where it's a list** of error objects. Key distinction for client parsing.
10. **Timestamps** — ISO 8601 with UTC offset via `datetime.isoformat()` + `USE_TZ=True`. Fields: `created_at`, `updated_at`, `archived_at` (latter null when active).
11. **IDs** — UUID v4, server-assigned, lowercase. Router validates with `<uuid:...>`.
12. **Idempotency patterns** — archive-of-archived, terminate-of-terminated, prompt-on-running, prompt-on-terminated, delete-of-running-session, delete-of-env-with-sessions, create-session-with-archived-agent/env → all `409`. Delete-of-missing → `404`.

## Cross-Track Discoveries

- **Agent `environment_id` ↔ session environment resolution**: an agent may reference a default environment; session creation can override via `environment_id` in the request body (`views.py:196-206`). Both paths flow through the same networking enforcement.
- **System prompt + multi-turn**: baked on session create only. Clients doing multi-turn must NOT assume the system prompt re-applies — it's part of turn-1 state in the Sprite's history (`views.py:241-244`, `views.py:409-413`).
- **Metadata semantics diverge between resources**: agents use per-key merge with `""` = delete; environments' `env_vars` uses full replacement. Easy trap for clients.
- **Session creation is 202 not 201**: background thread runs execution. Clients must consume the stream (or poll GET) to observe completion.
- **Stream replay + terminate**: a terminated session's stream replays everything then emits `terminated` event and closes. Useful for post-hoc audit.

## Code References

| File | Tracks | Role |
|------|--------|------|
| `src/fairy/urls.py` | 1,2,3,4 | Route table (only source of truth) |
| `src/fairy/views.py:146-164` | 3 | `RunRequest` (session create body) |
| `src/fairy/views.py:227-308` | 3 | Session create flow + sprite wiring |
| `src/fairy/views.py:334-358` | 3 | SSE stream endpoint |
| `src/fairy/views.py:376-439` | 3 | Multi-turn prompt, continue_session=True |
| `src/fairy/views.py:442-489` | 3 | Terminate + delete |
| `src/fairy/views.py:557-579` | 1 | `_validate_mcp_servers` |
| `src/fairy/views.py:582-646` | 1 | Create/Update agent request models |
| `src/fairy/views.py:649-684` | 1 | Agent + version serializers |
| `src/fairy/views.py:726-870` | 1 | Agent CRUD views |
| `src/fairy/views.py:875-971` | 2 | Environment request models + serializers |
| `src/fairy/views.py:985-1148` | 2 | Environment CRUD + versions |
| `src/fairy/views.py:71-82` | 2,3 | Networking policy wiring |
| `src/fairy/stream.py:50-175` | 3 | Background runner + SSE event emission |
| `src/fairy/sprites_exec.py:260-318` | 2,3 | `build_wrapper_script` — full execution order |
| `src/fairy/runtimes.py:5-87` | 1 | `AgentModel` + `RUNTIMES` + `MODEL_RUNTIME_MAP` |
| `src/fairy/auth.py:19-37` | 4 | Bearer-token validation |
| `src/fairy/models.py:34-48` | 4 | `APIKey.create_key` |
| `src/fairy/models.py:78-109` | 3 | `AgentSession` + status choices |
| `src/fairy/models.py:142-172` | 2 | `Environment` model incl. networking split |
| `tests/e2e/test_agents.py:168-182` | 1 | Metadata merge behavior proof |
| `tests/e2e/test_sessions.py:219-254` | 3 | Multi-turn filesystem persistence proof |
| `tests/e2e/test_sessions.py:158-164` | 3 | Stream replay after completion |
| `tests/e2e/test_sessions.py:183-207` | 3 | Terminate idempotent-error |

## Deliverable

Single file `docs/API.md`, organized as:

1. Quickstart — auth, base URL, /health
2. Conventions — error envelope, versioning, metadata, idempotency
3. Agents lifecycle — create, list, get, PUT (with version), archive, versions, MCP servers, runtime/model table
4. Environments lifecycle — create, packages, env_vars semantics, setup_script, networking (unrestricted/limited), PUT full-replacement caveat, archive vs delete
5. Sessions lifecycle — create (202), state machine diagram, SSE consumption pattern, multi-turn, terminate, delete, worked end-to-end
6. Runtime/model matrix
7. Error code table

## Related Research

- `thoughts/research/2026-04-16-environment-model.md` — deeper model-level detail on environments
- `thoughts/research/2026-04-16-session-based-execution.md` — execution mechanics
- `thoughts/research/2026-04-16-agent-tools-and-mcp.md` — MCP server config deep dive
- `thoughts/research/2026-04-17-network-isolation.md` — limited networking policy details
- `thoughts/research/2026-04-17-agent-skills-support.md` — skills field details

## Resolved Questions

Confirmed by maintainer (2026-04-17):

- **No `GET /sessions` list endpoint — intentional.** Clients are expected to track session IDs themselves. Do not propose adding a list endpoint.
- **`env_vars` plaintext-at-rest — known, not worth closing right now.** API contract hides the values; DB-level encryption is deferred. Don't open a PR for this unless asked.
- **Independent runtime/model validation — leave as-is.** Mismatches surface at session exec time; that's accepted.
