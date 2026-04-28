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
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
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

## Pretty-printing agent output

`aod.pretty` holds optional, **runtime-scoped** formatters that turn an
agent's raw stdout into human-readable display lines. Runtime output formats
are not part of the AoD API, so these helpers live under a separate namespace
— the core SDK stays runtime-agnostic.

Currently shipped:

| Formatter                       | Runtime         | Input format                  |
| ------------------------------- | --------------- | ----------------------------- |
| `aod.pretty.claude.ClaudeFormatter` | `claude-*`  | Claude CLI `stream-json` lines |

```python
from aod import Client
from aod.pretty.claude import ClaudeFormatter

fmt = ClaudeFormatter()
with client.sessions.stream(session_id) as events:
    for event in events:
        for line in fmt.consume(event):  # filters to output/stdout
            print(line)
    for line in fmt.flush():              # drain any unterminated buffer
        print(line)
```

`.consume(event)` is the event-oriented entry point. `.feed(chunk)` and
`.flush()` are available for lower-level integrations that already extracted
the stdout bytes themselves.

## Development

```bash
cd clients/python
uv pip install -e ".[dev]"
pytest
ruff check
mypy
```

## Releases (maintainers)

Published to PyPI via GitHub Actions using [Trusted
Publishing](https://docs.pypi.org/trusted-publishers/) — no API tokens to
manage. Two workflows live in `.github/workflows/`:

- `sdk-release.yml` — fires on GitHub Release creation with tag
  `aod-sdk-v<version>`, publishes to [pypi.org/p/aod-sdk](https://pypi.org/p/aod-sdk).
- `sdk-release-test.yml` — manual `workflow_dispatch`, publishes to
  [test.pypi.org/p/aod-sdk](https://test.pypi.org/p/aod-sdk) for dry-run
  validation.

### One-time setup

1. **Reserve the name on PyPI (and TestPyPI)**. On both
   [pypi.org](https://pypi.org/manage/account/publishing/) and
   [test.pypi.org](https://test.pypi.org/manage/account/publishing/), add a
   *pending* trusted publisher:

   | Field | Value |
   | ----- | ----- |
   | Project name | `aod-sdk` |
   | Owner | `ravi-hq` |
   | Repository | `agent-on-demand` |
   | Workflow | `sdk-release.yml` (PyPI) / `sdk-release-test.yml` (TestPyPI) |
   | Environment | `pypi` / `testpypi` |

2. **(Recommended) Create protected GitHub environments**. In
   *Settings → Environments*, create `pypi` with required reviewers, and
   `testpypi` with no protections.

### Cutting a release

1. Bump the version in **both** `clients/python/pyproject.toml` and
   `clients/python/src/aod/__init__.py` (the workflow verifies the
   pyproject value matches the tag). PR + merge to `main`.
2. (Recommended) Run `sdk-release-test.yml` against `main` and
   `pip install -i https://test.pypi.org/simple/ aod-sdk==<version>` in a
   throwaway venv to confirm the wheel installs and imports cleanly.
3. Tag and create a GitHub Release:

   ```bash
   gh release create aod-sdk-v0.2.0 \
     --title "aod-sdk v0.2.0" \
     --notes "..." \
     --target main
   ```

   `sdk-release.yml` fires on `published`, verifies the tag matches
   `pyproject.toml`, runs the tests, builds sdist + wheel, and uploads to
   PyPI.
