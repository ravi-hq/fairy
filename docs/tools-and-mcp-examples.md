# Agents with tools and MCP servers — curl examples

Copy-pasteable `curl` calls for every meaningful shape of `tools` and
`mcp_servers` on `POST /agents`. Aimed at humans who want to see the request
body, not fully-scripted clients.

Set these once per shell:

```bash
export FAIRY_URL=http://localhost:8777   # what `make dev` serves
export FAIRY_TOKEN=<your-api-token>
```

All examples below target the running dev server. Enforcement per runtime is
summarised in the capability table in the main `README.md`.

## Built-in toolset (`agent_toolset_20260401`)

Canonical tool names: `bash`, `read`, `write`, `edit`, `glob`, `grep`,
`web_fetch`, `web_search`. Omit `tools` entirely to get the runtime default
(everything on).

### 1. Allow all built-in tools (explicit)

Equivalent to omitting `tools`. Good starting shape.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "unrestricted",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{"type": "agent_toolset_20260401"}]
  }'
```

### 2. Deny all built-in tools

Sandboxed agent with no file/shell/web access. Useful for pure-reasoning
agents or agents whose only capability is a single MCP server.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "sandboxed",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{
      "type": "agent_toolset_20260401",
      "default_config": {"enabled": false}
    }]
  }'
```

### 3. Allowlist: only `bash`, `read`, `grep`

`default_config.enabled=false` flips the policy to deny-by-default; `configs`
entries with `enabled:true` punch holes in the deny wall.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "read-only-shell",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{
      "type": "agent_toolset_20260401",
      "default_config": {"enabled": false},
      "configs": [
        {"name": "bash",  "enabled": true},
        {"name": "read",  "enabled": true},
        {"name": "grep",  "enabled": true}
      ]
    }]
  }'
```

### 4. Denylist: everything except web access

Default allow, disable the code-manipulation tools individually. Useful for a
research agent that shouldn't touch files.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "researcher",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{
      "type": "agent_toolset_20260401",
      "configs": [
        {"name": "bash",  "enabled": false},
        {"name": "edit",  "enabled": false},
        {"name": "write", "enabled": false},
        {"name": "glob",  "enabled": false},
        {"name": "grep",  "enabled": false},
        {"name": "read",  "enabled": false}
      ]
    }]
  }'
```

## Updating tools on an existing agent

`PUT /agents/{id}` requires the current `version` for optimistic concurrency.
The response returns the bumped version; re-sending with the stale `version`
returns `409`.

```bash
curl -sS -X PUT "$FAIRY_URL/agents/$AGENT_ID" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "version": 1,
    "tools": [{
      "type": "agent_toolset_20260401",
      "default_config": {"enabled": false},
      "configs": [{"name": "read", "enabled": true}]
    }]
  }'
```

Only fields present in the body are changed; others are left alone. To clear
tools, send `"tools": []`.

## Validation errors

Each of the following returns `422 Unprocessable Entity`. Copy-paste and
compare the `detail` message to confirm your client's error handling works.

### A. Unknown tool type (including `custom`, which was removed)

`custom` tools were accepted in prior versions but are no longer valid.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bad",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{
      "type": "custom",
      "name": "get_weather",
      "description": "...",
      "input_schema": {"type": "object"}
    }]
  }'
```

### B. `mcp_toolset` missing `mcp_server_name`

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "bad",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "tools": [{"type": "mcp_toolset"}]
  }'
```
