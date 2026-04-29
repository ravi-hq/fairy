# Quickstart

This page walks through the minimum-viable flow: create an agent, start a session, and stream its output. All you need is a running Agent on Demand deployment and an API token.

Python examples use the official [`aod-sdk`](../sdks/python.md) package (`pip install aod-sdk`). `Client()` reads `AOD_API_URL` and `AOD_API_TOKEN` from the environment when no explicit arguments are passed.

## Prerequisites

Choose a deployment:

- **Hosted API** (`https://aod.ravi.id`): [sign up](https://aod.ravi.id/ui/register) — your token is shown once on the welcome screen. Create more at `/ui/api-keys`.
- **Local dev**: run `make dev` (server on `http://localhost:8777`), then register at `http://localhost:8777/ui/register` to get a token.
- **Self-hosted**: set `BASE` to your deployment URL and follow the same sign-up flow.

=== "curl"

    ```bash
    BASE=https://aod.ravi.id   # or http://localhost:8777 for local dev
    TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ```

=== "Python"

    ```bash
    export AOD_API_URL=https://aod.ravi.id   # or http://localhost:8777 for local dev
    export AOD_API_TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    pip install aod-sdk
    ```

## Step 1 — Create an agent

An agent is a reusable template. The minimum required fields are `name`, `model`, and `runtime`.

=== "curl"

    ```bash
    curl -X POST "$BASE/agents" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{
        "name": "hello",
        "model": "anthropic/claude-sonnet-4-6",
        "runtime": "claude"
      }'
    ```

=== "Python"

    ```python
    from aod import Client

    client = Client()  # reads AOD_API_URL + AOD_API_TOKEN
    agent = client.agents.create(
        name="hello",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
    )
    print(agent.id)
    ```

Response (`201 Created`):

```json
{
  "id": "<agent-uuid>",
  "type": "agent",
  "name": "hello",
  "model": "anthropic/claude-sonnet-4-6",
  "runtime": "claude",
  "system": null,
  "description": null,
  "environment_id": null,
  "skills": [],
  "mcp_servers": [],
  "metadata": {},
  "version": 1,
  "created_at": "2026-04-17T14:00:00.000000+00:00",
  "updated_at": "2026-04-17T14:00:00.000000+00:00",
  "archived_at": null
}
```

Save the `id` — you'll need it to create a session.

## Step 2 — Create a session

A session runs the agent with a prompt inside a Sprite. Execution is asynchronous; the response comes back immediately with `status: "pending"`.

=== "curl"

    ```bash
    curl -X POST "$BASE/sessions" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{
        \"agent_id\": \"<agent-uuid>\",
        \"prompt\": \"Print the current date.\",
        \"timeout\": 120
      }"
    ```

=== "Python"

    ```python
    ack = client.sessions.create(
        agent_id=agent.id,
        prompt="Print the current date.",
        timeout=120,
    )
    print(ack.id, ack.status)  # <session-uuid> pending
    ```

Response (`202 Accepted`):

```json
{
  "id": "<session-uuid>",
  "status": "pending",
  "stream_url": "/sessions/<session-uuid>/stream",
  "environment_id": null,
  "resources": [],
  "current_turn": 1
}
```

## Step 3 — Stream output

Connect to the SSE stream to receive agent output in real time.

=== "curl"

    The `-N` flag disables curl's output buffering.

    ```bash
    curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/<session-uuid>/stream"
    ```

    You'll see a sequence of `data:` lines:

    ```
    data: {"type":"start","runtime":"claude","session_id":"<session-uuid>"}

    data: {"type":"turn_start","id":1,"turn":1}

    id: 1
    data: {"type":"output","id":1,"stream":"stdout","data":"Thu Apr 17 14:00:00 UTC 2026\n","turn":1}

    id: 2
    data: {"type":"exit","id":2,"code":0}
    ```

=== "Python"

    `client.sessions.stream(session_id)` is a context manager yielding typed `StreamEvent` objects.

    ```python
    with client.sessions.stream(ack.id) as events:
        for event in events:
            if event.type == "output":
                print(event.extra["data"], end="")
            elif event.type == "exit":
                print(f"\n[exit {event.extra['code']}]")
    ```

The stream closes after the terminal event (`exit`, `error`, or `terminated`). Reconnecting replays all output from the beginning — useful if your connection drops.

## Full scripted example

=== "curl"

    ```bash
    BASE=http://localhost:8777
    TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    AUTH="Authorization: Bearer $TOKEN"
    JSON="Content-Type: application/json"

    # Create agent
    AGENT_ID=$(curl -s -X POST "$BASE/agents" -H "$AUTH" -H "$JSON" \
      -d '{"name":"demo","model":"anthropic/claude-sonnet-4-6","runtime":"claude"}' \
      | jq -r .id)

    # Create session
    SESS_ID=$(curl -s -X POST "$BASE/sessions" -H "$AUTH" -H "$JSON" \
      -d "{\"agent_id\":\"$AGENT_ID\",\"prompt\":\"Say hello.\",\"timeout\":120}" \
      | jq -r .id)

    # Stream output
    curl -N -H "$AUTH" "$BASE/sessions/$SESS_ID/stream"

    # Clean up
    curl -s -X POST -H "$AUTH" "$BASE/agents/$AGENT_ID/archive"
    ```

=== "Python"

    ```python
    from aod import Client

    with Client() as client:
        agent = client.agents.create(
            name="demo", model="anthropic/claude-sonnet-4-6", runtime="claude"
        )
        ack = client.sessions.create(
            agent_id=agent.id, prompt="Say hello.", timeout=120
        )
        with client.sessions.stream(ack.id) as events:
            for event in events:
                if event.type == "output":
                    print(event.extra["data"], end="")
                elif event.type == "exit":
                    break
        client.agents.archive(agent.id)
    ```

## What's next

- [Core Concepts](concepts.md) — understand agents, environments, sessions, and versioning.
- [API Reference](reference.md) — browse all endpoints interactively.
- [Python SDK](../sdks/python.md) — full surface, typed errors, async client, SSE helpers.
- [Streaming](streaming.md) — full SSE event reference and reconnect guidance.
