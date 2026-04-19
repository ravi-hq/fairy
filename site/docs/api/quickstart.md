# Quickstart

This page walks through the minimum-viable flow: create an agent, start a session, and stream its output. All you need is a running Agent on Demand deployment and an API token.

## Prerequisites

- **Local**: run `make dev` — the server starts on `http://localhost:8777`.
- **Remote**: set `BASE` to your deployment URL.
- A valid API token (created server-side via `APIKey.create_key`).

```bash
BASE=http://localhost:8777
TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Step 1 — Create an agent

An agent is a reusable template. The minimum required fields are `name`, `model`, and `runtime`.

```bash
curl -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hello",
    "model": "claude-sonnet-4-6",
    "runtime": "claude"
  }'
```

Response (`201 Created`):

```json
{
  "id": "<agent-uuid>",
  "type": "agent",
  "name": "hello",
  "model": "claude-sonnet-4-6",
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

Response (`202 Accepted`):

```json
{
  "id": "<session-uuid>",
  "status": "pending",
  "stream_url": "/sessions/<session-uuid>/stream",
  "environment_id": null,
  "resources": []
}
```

## Step 3 — Stream output

Connect to the SSE stream to receive agent output in real time. The `-N` flag disables curl's output buffering.

```bash
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/<session-uuid>/stream"
```

You'll see a sequence of `data:` lines:

```
data: {"type":"start","runtime":"claude","session_id":"<session-uuid>"}

data: {"type":"output","stream":"stdout","data":"Thu Apr 17 14:00:00 UTC 2026\n"}

data: {"type":"exit","code":0}
```

The stream closes after the terminal event (`exit`, `error`, or `terminated`). Reconnecting replays all output from the beginning — useful if your connection drops.

## Full scripted example

```bash
BASE=http://localhost:8777
TOKEN=aod_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
AUTH="Authorization: Bearer $TOKEN"
JSON="Content-Type: application/json"

# Create agent
AGENT_ID=$(curl -s -X POST "$BASE/agents" -H "$AUTH" -H "$JSON" \
  -d '{"name":"demo","model":"claude-sonnet-4-6","runtime":"claude"}' \
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

## What's next

- [Core Concepts](concepts.md) — understand agents, environments, sessions, and versioning.
- [API Reference](reference.md) — browse all endpoints interactively.
- [Streaming](streaming.md) — full SSE event reference and reconnect guidance.
