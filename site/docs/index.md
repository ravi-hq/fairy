# Agent on Demand

Agent on Demand is a REST API for running AI coding agents on [Sprites](https://sprites.dev). It manages three resources:

- **Agents** — reusable templates that define the model, runtime, system prompt, MCP servers, and skills for an AI coding agent.
- **Environments** — Sprite sandbox configurations: packages to install, environment variables to export, a setup script, and a network policy.
- **Sessions** — one execution of an agent inside a Sprite. Sessions are async; output is consumed via a Server-Sent Events stream. After a session completes, send a follow-up prompt to continue in the same Sprite with the same filesystem and history.

Local dev runs on `http://localhost:8777` (`make dev`). Every request except `GET /health` requires a Bearer token.

## Quickstart

Three calls to go from zero to a running agent:

=== "curl"

    ```bash
    BASE=http://localhost:8777
    TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

    # 1. Create an agent.
    AGENT_ID=$(curl -s -X POST "$BASE/agents" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"name":"hello","model":"anthropic/claude-sonnet-4-6","runtime":"claude"}' | jq -r .id)

    # 2. Start a session.
    SESS_ID=$(curl -s -X POST "$BASE/sessions" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"agent_id\":\"$AGENT_ID\",\"prompt\":\"Say hello.\",\"timeout\":120}" | jq -r .id)

    # 3. Stream output.
    curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/$SESS_ID/stream"
    ```

=== "Python"

    ```bash
    pip install aod-sdk
    ```

    ```python
    from aod import Client

    # Client() reads AOD_API_URL and AOD_API_TOKEN from the environment.
    with Client(base_url="http://localhost:8777", token="aod_...") as client:
        agent = client.agents.create(
            name="hello", model="anthropic/claude-sonnet-4-6", runtime="claude"
        )
        ack = client.sessions.create(
            agent_id=agent.id, prompt="Say hello.", timeout=120
        )
        with client.sessions.stream(ack.id) as events:
            for event in events:
                if event.type == "output":
                    print(event.extra["data"], end="")
    ```

    See the [Python SDK](sdks/python.md) page for the full surface.

## Explore the docs

| Section | What you'll find |
|---------|-----------------|
| [Why Agent on Demand](api/why.md) | The case for agents-as-primitive — what becomes possible to build |
| [Quickstart](api/quickstart.md) | Full minimum-viable flow with curl + Python SDK |
| [Core Concepts](api/concepts.md) | Resources, versioning, state machines, metadata semantics |
| [API Reference](api/reference.md) | Interactive Stoplight Elements explorer |
| [Python SDK](sdks/python.md) | `aod-sdk` — typed sync + async client on PyPI |
| [TypeScript SDK](sdks/typescript.md) | `@ravi-hq/aod-sdk` — typed async client on npm, browser-compatible |
| [Authentication](api/authentication.md) | Bearer tokens, 401 shapes |
| [Streaming](api/streaming.md) | SSE event types, reconnect, replay |
| [Errors](api/errors.md) | Every status code and when it fires |
| [Pagination](api/pagination.md) | List envelope format |
| [Patterns](patterns/index.md) | Chat bot, CI bot, batch automation, CLI wrapper, dashboard |
| [Deploy Guide](operators/deploy.md) | Self-hosting: env vars, worker, production setup |
