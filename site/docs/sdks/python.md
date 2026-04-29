# Python SDK

The official Python client for Agent on Demand. Covers every endpoint in the [API reference](../api/reference.md) with typed pydantic models, sync and async clients, and a typed SSE event stream.

- **Package**: [`aod-sdk` on PyPI](https://pypi.org/p/aod-sdk)
- **Source**: [`clients/python/`](https://github.com/ravi-hq/agent-on-demand/tree/main/clients/python)
- **Supports**: Python 3.11+

## Install

```bash
pip install aod-sdk
```

## Quickstart

```python
from aod import Client

with Client(token="aod_...") as client:
    agent = client.agents.create(
        name="demo",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
    )
    ack = client.sessions.create(agent_id=agent.id, prompt="Say hello.")
    with client.sessions.stream(ack.id) as events:
        for event in events:
            if event.type == "output":
                print(event.extra["data"], end="")
```

`Client()` reads `AOD_API_URL` and `AOD_API_TOKEN` from the environment when no explicit arguments are passed.

## Feature summary

| Capability | Where it lives |
|------------|----------------|
| Sync + async clients | `aod.Client`, `aod.AsyncClient` |
| Typed resources | `client.agents`, `client.environments`, `client.sessions` |
| Typed models | `Agent`, `Environment`, `Session`, `SessionAck`, `SessionTurn`, `StreamEvent`, … |
| Typed error hierarchy | `AodError` → `NotFoundError`, `ConflictError`, `ValidationError`, `RateLimitError`, `AuthError`, `ServerError` |
| SSE stream (context manager, typed events) | `client.sessions.stream(session_id, since=None)` |
| Claude `stream-json` pretty-printer | `aod.pretty.claude.ClaudeFormatter` (optional, runtime-scoped) |

## Errors

Non-2xx responses raise a typed subclass of `AodHTTPError` — all share `.status_code`, `.detail`, `.method`, `.url`:

```python
from aod import Client, ConflictError

client = Client(token="aod_...")
try:
    client.agents.update(agent_id, version=1, name="renamed")
except ConflictError as e:
    # 409: stale version or archived row
    print(e.status_code, e.detail)
```

Mapping matches the [Errors reference](../api/errors.md):

| Status | Exception | When |
| ------ | --------- | ---- |
| 401 | `AuthError` | Missing or invalid token |
| 404 | `NotFoundError` | Resource missing (or not owned by caller) |
| 409 | `ConflictError` | Archived row, terminal session, or stale `version` |
| 422 | `ValidationError` | Server-side validation failure (see [Errors reference](../api/errors.md) for list vs string detail) |
| 429 | `RateLimitError` | Per-user concurrent session limit (`.limit`, `.active`) |
| 5xx | `ServerError` | |

## Streaming

`client.sessions.stream(session_id)` is a context manager that yields typed `StreamEvent` objects. See the [Streaming reference](../api/streaming.md) for the full event schema.

```python
with client.sessions.stream(session_id) as events:
    for event in events:
        match event.type:
            case "stage":
                print(f"[stage] {event.extra['stage']} {event.extra['state']}")
            case "output":
                print(event.extra["data"], end="")
            case "exit":
                print(f"\n[exit {event.extra['code']}]")
            case "error" | "terminated" | "stale":
                print(f"\n[{event.type}] {event.extra.get('message', '')}")
```

Pass `since=<id>` to resume after a previously-seen event.

### Pretty-printing Claude output

For Claude-runtime sessions, `aod.pretty.claude.ClaudeFormatter` parses the `stream-json` lines into human-readable summaries:

```python
from aod import Client
from aod.pretty.claude import ClaudeFormatter

fmt = ClaudeFormatter()
with client.sessions.stream(session_id) as events:
    for event in events:
        for line in fmt.consume(event):  # filters to output/stdout
            print(line)
    for line in fmt.flush():              # drain any buffered partial
        print(line)
```

Other runtimes emit plain text — no formatter needed.

## Async

Every method has an async counterpart on `AsyncClient`:

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

SSE streams use `async with client.sessions.stream(...)` and `async for event in events`.

## See also

- [`clients/python/README.md`](https://github.com/ravi-hq/agent-on-demand/tree/main/clients/python#readme) — full API surface, optimistic-concurrency semantics, release notes for maintainers.
- [Example CLI](https://github.com/ravi-hq/agent-on-demand/tree/main/examples/cli) — a production-ready CLI wrapper built on the SDK.
- [Quickstart](../api/quickstart.md), [Streaming](../api/streaming.md), [Authentication](../api/authentication.md) — Python examples are tabbed alongside curl.
