---
name: aod-sdk-python
description: Use when writing Python code that calls the Agent on Demand API via `aod-sdk` (package `aod` — `Client`/`AsyncClient`, `client.agents`/`environments`/`sessions`, `client.sessions.stream(...)`). Covers install, `AOD_API_URL`/`AOD_API_TOKEN` env fallbacks, typed pydantic models, sync-vs-async method parity, the `StreamEvent` SSE iterator (context manager + `.extra` dict), typed `AodHTTPError` subclasses (`ConflictError`/`ValidationError`/`RateLimitError.limit`/`active`), and the runtime-scoped `aod.pretty` formatters. Defers to the `agent-on-demand-api` skill for HTTP semantics, status codes, and state-machine edges.
---

# Agent on Demand Python SDK Skill

The `aod-sdk` Python package (import name `aod`) wraps every endpoint in `docs/openapi.yaml` with typed pydantic models, sync + async clients, and a typed SSE event stream. Package source lives at `clients/python/` in this repo.

## When This Skill Applies

Use this skill when:
- Writing Python that calls the AoD API via `from aod import Client` / `AsyncClient`
- Extending `clients/python/` itself (new resources, new models, new stream helpers)
- Debugging a traceback from `aod.errors.AodHTTPError` or its subclasses

For HTTP-level questions (route table, state machine, 409/422/429 semantics), defer to the `agent-on-demand-api` skill. For TypeScript, use `aod-sdk-typescript`.

## Install & Configure

```bash
pip install aod-sdk
# or, in-tree development:
cd clients/python && uv pip install -e ".[dev]"
```

`Client` / `AsyncClient` read these in order of precedence:
1. Constructor kwargs: `base_url=...`, `token=...`
2. Env vars: `AOD_API_URL`, `AOD_API_TOKEN`
3. `base_url` default: `http://localhost:8777`. `token` is **required** — missing raises `ValueError`, not a 401.

Both clients are context managers. Use them as such — they own an `httpx.Client`/`AsyncClient`.

```python
from aod import Client, AsyncClient

with Client(token="aod_...") as client:
    ...

async with AsyncClient() as client:  # reads AOD_API_TOKEN from env
    ...
```

## Resources Shape

Every client exposes three resource namespaces with identical method names on both sync and async variants:

| Namespace             | Methods                                                                                                                          |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `client.agents`       | `list()`, `create(...)`, `get(id)`, `update(id, version=..., **fields)`, `archive(id)`, `versions(id)`                            |
| `client.environments` | `list()`, `create(...)`, `get(id)`, `update(id, version=..., **fields)`, `archive(id)`, `delete(id)`, `versions(id)`              |
| `client.sessions`     | `list()`, `create(agent_id=..., prompt=..., [environment_id=], [timeout=], [resources=])`, `get(id)`, `prompt(id, prompt=...)`, `turns(id)`, `terminate(id)`, `delete(id)`, `stream(id, since=None)` |

- Every method accepts `str | UUID` for IDs; they're stringified on the wire.
- Return types are pydantic models from `aod.models` (`Agent`, `Environment`, `Session`, `SessionAck`, `SessionTurn`, `AgentVersion`, `EnvironmentVersion`). Unknown fields are **ignored** (`extra="ignore"`), so new server fields don't blow up old clients.
- `sessions.create` and `sessions.prompt` and `sessions.terminate` return `SessionAck` — a **trimmed** payload, not a full `Session`. Only `id` + `status` are guaranteed; `stream_url`/`environment_id`/`resources`/`current_turn` are populated when the server provides them. Fetch `client.sessions.get(id)` for the full record.

## Streaming

`client.sessions.stream(session_id, since=None)` is a **context manager** that yields a **`StreamEvent` iterator**. The nesting matters — it owns an HTTP connection:

```python
with client.sessions.stream(session_id) as events:
    for event in events:
        if event.type == "output":
            print(event.extra["data"], end="")
        elif event.type in ("exit", "error", "terminated", "stale"):
            break
```

Async:

```python
async with client.sessions.stream(session_id) as events:
    async for event in events:
        ...
```

`StreamEvent` shape: `type: StreamEventType` + `id: int | None` + `extra: dict[str, Any]`. Every server-side field except `type` and `id` lands in `extra`. This is deliberate — the event schema is still evolving, and keeping the raw payload accessible avoids breakage.

Event types: `start`, `turn_start`, `output`, `stage`, `exit`, `error`, `terminated`, `stale`. Terminal types are `exit`/`error`/`terminated`/`stale` — break the loop when you see one.

To resume after a disconnect, pass `since=<last event id>`. `since=None` / omitted = full replay.

## Errors

All non-2xx responses raise a typed subclass of `AodError`:

| Status | Exception         | Common trigger                                                |
| ------ | ----------------- | ------------------------------------------------------------- |
| 401/3  | `AuthError`       | Missing/invalid token                                         |
| 404    | `NotFoundError`   | Resource missing or not owned by the token's user             |
| 409    | `ConflictError`   | Archive-already, terminal session, stale `version`, failed-resume |
| 422    | `ValidationError` | Pydantic validation on the server — `detail` is a **list**    |
| 429    | `RateLimitError`  | Concurrent-session quota. Has `.limit` and `.active` attrs    |
| 5xx    | `ServerError`     | Sprites upstream error or unhandled exception                 |

All share `.status_code`, `.detail`, `.method`, `.url`. `detail` is whatever the server sent — a string for most codes, a **list of error dicts** for 422 (Pydantic). Match with `isinstance` rather than on `status_code`.

```python
from aod import ConflictError, RateLimitError

try:
    client.agents.update(agent.id, version=agent.version, name="renamed")
except ConflictError as e:
    # stale version — refetch and retry
    latest = client.agents.get(agent.id)
    client.agents.update(latest.id, version=latest.version, name="renamed")
except RateLimitError as e:
    print(f"quota: {e.active}/{e.limit}")
```

## Optimistic Concurrency Idiom

Agents and environments require `version=<current>` on every update. Stale → `ConflictError`. Standard pattern is read-then-write:

```python
agent = client.agents.get(agent_id)
client.agents.update(agent.id, version=agent.version, system="...")
```

Merge/replace semantics match the HTTP API (covered in `agent-on-demand-api`):
- `metadata` is **merged per-key**; empty string deletes the key.
- `env_vars` is **fully replaced** — re-send every key you want to keep.

## Runtime-Scoped Pretty Printing

`aod.pretty` holds optional formatters that turn raw agent stdout into display lines. These are **runtime-specific** (runtime output formats are not part of the AoD API contract), so they live under a separate namespace and don't ship as part of the core `Client`.

```python
from aod.pretty.claude import ClaudeFormatter

fmt = ClaudeFormatter()
with client.sessions.stream(session_id) as events:
    for event in events:
        for line in fmt.consume(event):  # filters to output+stdout internally
            print(line)
    for line in fmt.flush():              # drain any half-buffered line
        print(line)
```

Currently shipped: `ClaudeFormatter` (consumes the Claude CLI `stream-json` output format). Other runtimes have no formatter yet — iterate `event.extra["data"]` yourself for `output` events.

## Common Gotchas

1. **Missing token is a `ValueError` at construction, not an `AuthError`.** You won't hit the network to discover the config is broken.
2. **`Session` response has no `prompt`.** `prompt` lives on `SessionTurn` — fetch it via `client.sessions.turns(session_id)`.
3. **`SessionAck.environment_id` is `None` on `prompt` / `terminate` acks.** It's only set on `create` because that's the only ack where the server knows it needs to echo it.
4. **The stream context manager must be exited for the connection to close.** Breaking out of the inner `for` is fine; don't hold the `events` iterator past the `with` block.
5. **`extra["data"]` on `output` events is a string.** `stream`/`turn` also live in `extra`. Don't assume a fixed schema — consult `agent-on-demand-api` for the per-event payload shape.
6. **Sync vs async symmetry is strict.** Every `Client` method has an `AsyncClient` counterpart with the same name and signature — except `close()` → `aclose()` and context manager forms.

## End-to-End Example

```python
from aod import Client

with Client(token="aod_...") as client:
    env = client.environments.create(
        name="demo",
        packages={"pip": ["requests"]},
        env_vars={"DEMO": "1"},
        networking={"type": "limited", "allowed_hosts": ["pypi.org"]},
    )
    agent = client.agents.create(
        name="demo",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        system="You are terse.",
        environment_id=env.id,
    )
    ack = client.sessions.create(
        agent_id=agent.id,
        prompt="summarize README.md",
        resources=[{"type": "github_repository", "url": "https://github.com/me/repo"}],
    )
    with client.sessions.stream(ack.id) as events:
        for event in events:
            if event.type == "output":
                print(event.extra["data"], end="")
            elif event.type in ("exit", "error", "terminated", "stale"):
                break
    final = client.sessions.get(ack.id)
    print(f"status={final.status} exit_code={final.exit_code}")
```

## Related Files

- `clients/python/src/aod/client.py` — `Client` / `AsyncClient`; config resolution
- `clients/python/src/aod/resources/` — `agents.py`, `environments.py`, `sessions.py`
- `clients/python/src/aod/models.py` — pydantic models (`Agent`, `Session`, `StreamEvent`, etc.)
- `clients/python/src/aod/errors.py` — exception hierarchy + `raise_for_status`
- `clients/python/src/aod/stream.py` — sync/async SSE iterators
- `clients/python/src/aod/pretty/` — runtime-scoped formatters (`claude.py`)
- `clients/python/README.md` — user-facing docs
- Sibling skill `agent-on-demand-api` — HTTP semantics, status codes, state machine
