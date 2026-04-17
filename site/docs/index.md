# Fairy

Fairy is a REST API for running AI coding agents on [Sprites](https://fly.io/docs/machines/). It manages three resources:

- **Agents** — reusable templates that define the model, runtime, system prompt, MCP servers, and skills for an AI coding agent.
- **Environments** — Sprite sandbox configurations: packages to install, environment variables to export, a setup script, and a network policy.
- **Sessions** — one execution of an agent inside a Sprite. Sessions are async; output is consumed via a Server-Sent Events stream.

Local dev runs on `http://localhost:8777` (`make dev`). Every request except `GET /health` requires a Bearer token.

## Quickstart

Three calls to go from zero to a running agent:

```bash
BASE=http://localhost:8777
TOKEN=fairy_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 1. Create an agent.
AGENT_ID=$(curl -s -X POST "$BASE/agents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"hello","model":"claude-sonnet-4-6","runtime":"claude"}' | jq -r .id)

# 2. Start a session.
SESS_ID=$(curl -s -X POST "$BASE/sessions" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\":\"$AGENT_ID\",\"prompt\":\"Say hello.\",\"timeout\":120}" | jq -r .id)

# 3. Stream output.
curl -N -H "Authorization: Bearer $TOKEN" "$BASE/sessions/$SESS_ID/stream"
```

## Explore the docs

| Section | What you'll find |
|---------|-----------------|
| [Quickstart](api/quickstart.md) | Full minimum-viable flow with curl commands |
| [Core Concepts](api/concepts.md) | Resources, versioning, state machines, metadata semantics |
| [Overview](api/product.md) | What problems fairy solves |
| [API Reference](api/reference.md) | Interactive Stoplight Elements explorer |
| [Authentication](api/authentication.md) | Bearer tokens, 401 shapes |
| [Streaming](api/streaming.md) | SSE event types, reconnect, replay |
| [Errors](api/errors.md) | Every status code and when it fires |
| [Pagination](api/pagination.md) | List envelope format |
