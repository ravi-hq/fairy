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

## MCP servers

Agents can declare MCP servers under `mcp_servers`. Each entry has a
`name` (unique per agent) and either `type: "url"` or `type: "stdio"`.
Max 20 servers per agent.

### 5. URL MCP server

Declare a remote HTTP MCP server. With no matching `mcp_toolset` entry,
all tools the server exposes are available to the agent.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "with-github-mcp",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [
      {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}
    ]
  }'
```

### 6. URL MCP server with auth headers

Headers are forwarded on every MCP request. `${VAR}` references are
substituted from the agent's environment `env_vars` at session start, so
you don't have to embed secrets in the agent definition itself.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "with-auth-mcp",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{
      "type": "url",
      "name": "github",
      "url": "https://mcp.github.com/mcp",
      "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}
    }]
  }'
```

### 7. stdio MCP server

Launches a local process inside the Sprite and speaks MCP over its
stdin/stdout. Good for filesystem / git / database MCP servers that run
locally.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "with-fs-mcp",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{
      "type": "stdio",
      "name": "fs",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"],
      "env": {"NODE_ENV": "production"}
    }]
  }'
```

### 8. Multiple MCP servers

Any mix of URL and stdio servers on one agent.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "multi-mcp",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [
      {"type": "url",   "name": "github", "url": "https://mcp.github.com/mcp"},
      {"type": "stdio", "name": "fs",     "command": "npx",
       "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]}
    ]
  }'
```

## Restricting MCP servers with `mcp_toolset`

Add an `mcp_toolset` entry to `tools` to allow/deny individual tools on a
named server. Every `mcp_server_name` must reference an entry in
`mcp_servers` (or the request 422s — see Error C below).

The shape:

```json
{
  "type": "mcp_toolset",
  "mcp_server_name": "<server-name>",
  "default_config": {"enabled": true | false},
  "configs": [{"name": "<tool>", "enabled": true | false}, ...]
}
```

`default_config.enabled` sets the policy for tools not listed in
`configs`; `configs` entries override per-tool.

### 9. Allow a server (explicit, equivalent to declaring it with no toolset)

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "allow-github",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}],
    "tools": [{"type": "mcp_toolset", "mcp_server_name": "github"}]
  }'
```

### 10. Deny a whole server

Server stays declared (so the agent knows it *could* exist), but all its
tools are blocked. Useful for staged rollouts — flip `enabled` to `true`
later without touching the agent-version history for `mcp_servers`.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "deny-github",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}],
    "tools": [{
      "type": "mcp_toolset",
      "mcp_server_name": "github",
      "default_config": {"enabled": false}
    }]
  }'
```

### 11. Per-tool denylist on a server

Default-allow, disable individual dangerous tools.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "safe-github",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}],
    "tools": [{
      "type": "mcp_toolset",
      "mcp_server_name": "github",
      "configs": [
        {"name": "delete_repository", "enabled": false},
        {"name": "force_push",        "enabled": false}
      ]
    }]
  }'
```

### 12. Per-tool allowlist on a server

Deny-by-default, with a short list of explicitly-enabled tools. This is
the shape most real deployments will use.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "minimal-github",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"}],
    "tools": [{
      "type": "mcp_toolset",
      "mcp_server_name": "github",
      "default_config": {"enabled": false},
      "configs": [
        {"name": "search_issues", "enabled": true},
        {"name": "get_pr",        "enabled": true}
      ]
    }]
  }'
```

## Combining built-in + MCP toolsets

### 13. Restricted built-ins + allowlisted MCP server

A realistic production shape: narrow the built-in toolset, declare an
MCP server, and allowlist a few of its tools.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "scoped-github-pr-agent",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [{
      "type": "url",
      "name": "github",
      "url": "https://mcp.github.com/mcp",
      "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}
    }],
    "tools": [
      {
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": false},
        "configs": [
          {"name": "read", "enabled": true},
          {"name": "grep", "enabled": true}
        ]
      },
      {
        "type": "mcp_toolset",
        "mcp_server_name": "github",
        "default_config": {"enabled": false},
        "configs": [
          {"name": "get_pr",      "enabled": true},
          {"name": "list_pr_files","enabled": true},
          {"name": "create_review","enabled": true}
        ]
      }
    ]
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

### C. `mcp_toolset` referencing a server not in `mcp_servers`

Every `mcp_server_name` must match a declared server on the same agent.
This is checked on both create and update, using the merged effective
state — so removing a server while an old `mcp_toolset` still references
it also 422s.

```bash
curl -sS -X POST "$FAIRY_URL/agents" \
  -H "Authorization: Bearer $FAIRY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "dangling-ref",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "mcp_servers": [],
    "tools": [{"type": "mcp_toolset", "mcp_server_name": "ghost"}]
  }'
```

### D. More than 20 MCP servers

Fairy caps `mcp_servers` at 20 entries. Adding a 21st returns `422` with
a `detail` that mentions the limit.
