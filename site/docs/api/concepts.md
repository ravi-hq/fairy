# Core Concepts

## The three resources

### Agents

An **agent** is a reusable template that defines how an AI coding agent behaves:

- `model` + `runtime` — which AI model to use and which runtime to invoke it through.
- `system` — system prompt prepended to the first turn.
- `skills` + `mcp_servers` — additional capabilities made available to the agent.
- `environment_id` — an optional default environment (overridable per session).
- `metadata` — arbitrary flat key/value pairs for your own labeling.

Agents persist across sessions. You can run hundreds of sessions from a single agent.

### Environments

An **environment** describes the Sprite sandbox a session runs in:

- `packages` — software to install (`apt`, `pip`, `npm`, etc.).
- `env_vars` — environment variables to export (encrypted at rest, never returned in responses).
- `setup_script` — a bash script to run after packages are installed.
- `networking` — `unrestricted` (open) or `limited` (DNS allow-list).

Environments are optional. A session can run without one (the Sprite gets no extra setup).

### Sessions

A **session** is one execution of an agent inside a Sprite. Sessions are:

- **Async** — `POST /sessions` returns `202` immediately; the agent runs in the background.
- **Streamable** — `GET /sessions/{id}/stream` delivers output as Server-Sent Events.
- **Multi-turn** — after `completed`, you can `POST /sessions/{id}/prompt` to continue in the same Sprite with the same filesystem and runtime history. (`failed` and `terminated` sessions cannot be continued.)
- **Auditable** — `GET /sessions/{id}/turns` returns the full turn history: each turn's prompt, status, exit code, and timestamps (`started_at` / `ended_at` are nullable until the turn reaches that state).

## Session state machine

```
                POST /sessions
                     │
                     ▼
                ┌─────────┐
                │ pending │◄──────────────────────────────┐
                └────┬────┘                               │
                     │ worker picks it up                 │ POST /sessions/{id}/prompt
                     ▼                                    │ (resets to pending)
                ┌─────────┐                               │
                │ running │                               │
                └────┬────┘                               │
       ┌─────────────┼──────────────┐                     │
       │             │              │                     │
       ▼             ▼              ▼                     │
 ┌──────────┐  ┌────────┐  ┌──────────────┐              │
 │completed │  │ failed │  │  terminated  │◄─ POST …/terminate
 └────┬─────┘  └────────┘  └──────────────┘
      │
      └──────────────────────────────────────────────────┘
        POST /sessions/{id}/prompt (allowed from completed)
```

- `pending` → accepted, not yet executing. Cannot accept a new prompt; a turn is already queued. `POST /prompt` returns `409`.
- `running` → agent CLI is executing inside the Sprite. `POST /prompt` returns `409`.
- `completed` → runtime exited with code 0. Prompts accepted; session resets to `pending`.
- `failed` → runtime exited non-zero, or an unhandled exception. Cannot be continued; `POST /prompt` returns `409`.
- `terminated` → stopped by `POST /sessions/{id}/terminate`. Cannot be continued.

Only `completed` sessions accept `POST /sessions/{id}/prompt`. All other states (`pending`, `running`, `failed`, `terminated`) return `409`.

## Optimistic concurrency (agents and environments)

Agents and environments carry an integer `version` starting at 1. Every `PUT` that changes a field increments the version and writes a snapshot to the version history table.

You must echo the current version in every `PUT` body:

```json
{"version": 1, "name": "new-name"}
```

- **Match** → write succeeds, `version` becomes `N+1`.
- **Stale** → `409 {"detail": "Version mismatch: expected 3, got 2"}`. Re-fetch and retry.
- **No-op** → if all values are identical to the current state, version is not bumped.

Sessions do not have a `version`.

## Metadata merge semantics (agents only)

Agents carry a flat `metadata: {string: string}` field. Environments do not.

`PUT` applies **merge** semantics:

| Value in payload | Effect |
|-----------------|--------|
| Non-empty string | Upsert (replace or insert) |
| `""` (empty string) | Delete that key |
| Key omitted | Unchanged |

Example: current `{"team":"platform","env":"prod"}` + `PUT {"version":2,"metadata":{"env":"staging","team":""}}` → result `{"env":"staging"}`.

This is different from environment `env_vars`, which uses **full replacement** on `PUT` — any key you don't include is removed.

## Archive semantics

Both agents and environments support soft-archiving:

- `POST /{resource}/{id}/archive` — sets `archived_at` and hides the row from list endpoints.
- The resource is still accessible via `GET /{resource}/{id}`.
- There is no un-archive endpoint.
- Archiving an already-archived resource returns `409 {"detail":"... is already archived"}`.
- `PUT` on an archived resource returns `409`.

Environments also support hard delete (`DELETE /environments/{id}/delete`), which removes the record entirely. Hard delete is blocked if any session references the environment, even if the session has been deleted.

## IDs and timestamps

- **IDs**: UUID v4, lowercase, server-assigned. Never sent by the client.
- **Timestamps**: ISO 8601 with UTC offset — `2026-04-17T14:00:00.000000+00:00`.
- Timestamp fields: `created_at`, `updated_at`, `archived_at` (null while active).
