# aod-sdk

Python SDK for the [Agent on Demand](../../README.md) HTTP API. Covers every
endpoint in [`docs/openapi.yaml`](../../docs/openapi.yaml) with typed pydantic
models, sync and async clients, and a typed SSE event stream.

## Install

```bash
pip install aod-sdk
```

## Quickstart

### Sync

```python
from aod import Client

with Client(base_url="https://aod.example", token="aod_...") as client:
    env = client.environments.create(
        name="prod",
        packages={"apt": ["jq"], "npm": ["typescript"]},
        env_vars={"OPENAI_API_KEY": "sk-..."},
        networking={"type": "limited", "allowed_hosts": ["api.github.com"]},
    )
    agent = client.agents.create(
        name="my-agent",
        model="claude-sonnet-4-5",
        runtime="claude-code",
        system="You are a careful software engineer.",
        environment_id=env.id,
    )
    ack = client.sessions.create(
        agent_id=agent.id,
        prompt="implement the feature in TODO.md",
        resources=[
            {"type": "github_repository", "url": "https://github.com/me/repo"},
        ],
    )
    with client.sessions.stream(ack.id) as events:
        for event in events:
            print(event.type, event.extra)
```

### Async

```python
import asyncio
from aod import AsyncClient

async def main():
    async with AsyncClient(token="aod_...") as client:
        agents = await client.agents.list()
        for agent in agents:
            print(agent.name)

asyncio.run(main())
```

## Configuration

`base_url` and `token` can be passed to the constructor or read from the
`AOD_API_URL` and `AOD_API_TOKEN` environment variables. `base_url` defaults
to `http://localhost:8777`.

## Errors

Non-2xx responses raise a typed subclass of `AodHTTPError`:

| Status | Exception          | When                                                         |
| ------ | ------------------ | ------------------------------------------------------------ |
| 401/3  | `AuthError`        | Missing/invalid token                                        |
| 404    | `NotFoundError`    | Resource missing                                             |
| 409    | `ConflictError`    | Archived row, terminal session, or stale `version`           |
| 422    | `ValidationError`  | Server-side pydantic validation failure                      |
| 429    | `RateLimitError`   | Per-user concurrent session limit reached (`.limit`, `.active`) |
| 5xx    | `ServerError`      |                                                              |

All share `.status_code`, `.detail`, `.method`, `.url`.

## Optimistic concurrency

`agents` and `environments` require the current `version` on update. A stale
version raises `ConflictError`:

```python
agent = client.agents.get(agent_id)
client.agents.update(agent.id, version=agent.version, name="renamed")
```

## Streaming

`client.sessions.stream(session_id, since=None)` is a context manager that
yields a `StreamEvent` iterator. Pass `since` to resume after a specific event
id. Event types: `start`, `turn_start`, `output`, `stage`, `exit`, `error`,
`terminated`, `stale`. Everything except the discriminator (`type`) and
`id` lands in `event.extra`.

## Development

```bash
cd clients/python
uv pip install -e ".[dev]"
pytest
ruff check
mypy
```
