---
date: 2026-04-16T19:30:00-07:00
researcher: Claude Code (team-research skill)
git_commit: ecd660c63ceeacac5fa3478f3e0a99bd57902d2c
branch: add-environment-model
repository: ravi-hq/fairy
topic: "Adding tools and MCP servers to the Agent model"
tags: [research, team-research, agents, tools, mcp, managed-agents]
status: complete
method: agent-team
team_size: 3
tracks: [anthropic-tools-api, cli-runtime-integration, skills-api]
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: Adding Tools & MCP Servers to the Agent Model

**Date**: 2026-04-16
**Researcher**: Claude Code (team-research)
**Git Commit**: [`ecd660c`](https://github.com/ravi-hq/fairy/commit/ecd660c63ceeacac5fa3478f3e0a99bd57902d2c)
**Branch**: `add-environment-model`
**Repository**: ravi-hq/fairy
**Method**: Agent team (3 specialist researchers)

## Research Question

How should fairy add `tools` and `mcp_servers` fields to the Agent model, mirroring the Anthropic Managed Agents API, and how do those configurations map to the CLI runtimes (claude, codex, gemini) running inside Sprites?

## Summary

The Anthropic Managed Agents API defines three tool types (`agent_toolset_20260401`, `mcp_toolset`, `custom`) and a separate `mcp_servers` array for declaring MCP server connections. Each CLI runtime has a different mechanism for accepting MCP configuration: Claude uses `--mcp-config` with a JSON file, Codex uses `~/.codex/config.toml`, and Gemini uses `~/.gemini/settings.json`. fairy should store `tools` and `mcp_servers` as JSON fields on the Agent model, then generate the appropriate runtime-specific config files in the wrapper script. Custom tools require a callback mechanism (out of scope for v1). Skills are already handled by the existing `skills` JSON field.

## Research Tracks

### Track 1: Anthropic Tools & MCP API
**Scope**: Anthropic Managed Agents docs — tools reference, MCP connector, agent schema

#### Findings:

1. **Three tool types in `tools` array** — Discriminated union on `type` field: `agent_toolset_20260401` (built-in server-side tools), `mcp_toolset` (references an MCP server by name), `custom` (user-defined with JSON Schema input). Each entry in the array is independent.

2. **`agent_toolset_20260401` schema** — Contains 8 built-in tools: bash, read, write, edit, glob, grep, web_fetch, web_search. Supports `default_config` (enable/disable all, permission policy) and `configs` array for per-tool overrides. Permission policies: `always_allow` (default) or `always_ask`.

3. **`mcp_toolset` references `mcp_servers` by name** — The `mcp_toolset` entry in `tools` has `mcp_server_name` that must match a `name` in the agent's `mcp_servers` array. Defaults to `always_ask` permission policy (stricter than built-in tools).

4. **`mcp_servers` array schema** — Each entry: `{type: "url", name: "unique-name", url: "https://..."}`. Currently only `type: "url"` (remote HTTP) is supported. Max 20 servers per agent.

5. **Auth is NOT on agents** — MCP auth lives in vaults, referenced at session creation via `vault_ids`. Credential types: `mcp_oauth` (OAuth with refresh) and `mcp_bearer` (static token). Secret fields are write-only.

6. **Custom tools require callback infrastructure** — When invoked, the session emits `agent.custom_tool_use` and blocks until the app sends `user.custom_tool_result`. This is fundamentally different from CLI execution — fairy would need to implement a callback loop.

7. **Permission policies are Anthropic-specific** — `always_allow` vs `always_ask` controls whether the session pauses for user confirmation. In fairy's CLI-based execution, this maps to `--dangerously-skip-permissions` (always_allow) vs manual confirmation (not currently supported).

### Track 2: CLI Runtime Integration
**Scope**: How claude, codex, gemini CLIs accept tool/MCP configuration

#### Findings:

1. **Claude CLI: `--mcp-config` flag** — Best approach for containers. Write a JSON file with `mcpServers` object, pass via `--mcp-config /tmp/mcp.json`. Add `--strict-mcp-config` to ignore all other MCP sources. Supports both `stdio` and `http` server types.

   ```json
   {
     "mcpServers": {
       "server-name": {
         "type": "http",
         "url": "https://mcp.example.com/mcp",
         "headers": {"Authorization": "Bearer token"}
       }
     }
   }
   ```

2. **Claude CLI: `--tools` flag for built-in tools** — Restricts which built-in tools are available: `--tools "Bash,Edit,Read"`. Also `--disallowedTools` and `--allowedTools` for finer control.

3. **Codex CLI: `~/.codex/config.toml`** — Write TOML config before running. MCP servers declared as `[mcp_servers.name]` sections with `url`, `bearer_token_env_var`, `command`, `args`, etc.

   ```toml
   [mcp_servers.github]
   url = "https://mcp.github.com/mcp"
   bearer_token_env_var = "GITHUB_TOKEN"
   required = true
   ```

4. **Gemini CLI: `~/.gemini/settings.json`** — Write JSON config before running. Uses `mcpServers` object with `httpUrl` for HTTP servers, `command` for stdio servers.

   ```json
   {
     "mcpServers": {
       "github": {
         "httpUrl": "https://mcp.github.com/mcp",
         "headers": {"Authorization": "Bearer token"},
         "trust": true
       }
     }
   }
   ```

5. **Config file formats differ per runtime** — Claude uses `mcpServers` in JSON with `type`/`url`. Codex uses TOML with `url`/`command`. Gemini uses JSON with `httpUrl`/`command`. fairy's wrapper script generator must emit the right format per runtime.

6. **All CLIs support skipping permissions** — Claude: `--dangerously-skip-permissions`. Codex: `--full-auto` or `--dangerously-bypass-approvals-and-sandbox`. Gemini: `--approval-mode yolo`. Important for unattended container execution.

### Track 3: Skills API
**Scope**: Anthropic skills docs, relationship to tools and MCP

#### Findings:

1. **Skills are filesystem-based, not callable tools** — Skills are loaded into the container as files, read on demand (progressive disclosure). They are not function calls. This is fundamentally different from tools and MCP servers.

2. **Skills schema on agent** — Array of `{type, skill_id, version}`. Types: `anthropic` (pre-built: xlsx, pdf, docx, pptx) and `custom` (org-uploaded). Max 20 per agent.

3. **fairy's existing `skills` JSONField is correctly shaped** — The current `Agent.skills` field already stores this array format. No changes needed for skills.

4. **Skills, tools, and MCP servers are orthogonal** — Skills = context files loaded on demand. Tools = callable functions during conversation turns. MCP = external tool servers. They don't overlap.

## Cross-Track Discoveries

1. **fairy's architecture maps cleanly to Anthropic's model** — The Agent already has `skills` and `environment`. Adding `tools` and `mcp_servers` as JSON fields completes the parity. The wrapper script in `sprites_exec.py` is the translation layer between the Anthropic-style config and the runtime-specific CLI format.

2. **MCP auth needs a fairy-specific approach** — Anthropic uses vaults (a separate API resource). fairy could either: (a) store MCP auth tokens in `mcp_servers` config directly (simpler, less secure), (b) use env vars that reference `UserRuntimeKey` or a new credential store, or (c) accept tokens at session creation time (matching Anthropic's vault pattern). Option (b) is recommended — store MCP server URLs/names on the agent, inject auth via env vars in the wrapper script.

3. **Custom tools are out of scope for v1** — Custom tools (`type: "custom"`) require a real-time callback loop between the session and the caller. fairy's current architecture (background thread executing CLI, streaming logs) doesn't support injecting tool results mid-execution. This would require a fundamentally different execution model.

4. **`agent_toolset_20260401` is Anthropic-specific but maps to CLI flags** — The built-in toolset is only meaningful for the Managed Agents API. For CLI execution, the equivalent is the default set of tools each CLI provides. However, the `configs` array (enabling/disabling specific tools) maps to Claude's `--tools`/`--disallowedTools` flags. Codex and Gemini may have equivalent mechanisms.

## Code References

| File | Relevance | Link |
|------|-----------|------|
| `src/fairy/models.py:208-264` | Agent + AgentVersion models — add tools/mcp_servers fields here | [models.py](https://github.com/ravi-hq/fairy/blob/ecd660c/src/fairy/models.py) |
| `src/fairy/sprites_exec.py:105-153` | `build_wrapper_script` — add MCP config file generation here | [sprites_exec.py](https://github.com/ravi-hq/fairy/blob/ecd660c/src/fairy/sprites_exec.py) |
| `src/fairy/views.py:452-512` | Agent CRUD + serialization — add tools/mcp_servers to request/response | [views.py](https://github.com/ravi-hq/fairy/blob/ecd660c/src/fairy/views.py) |
| `src/fairy/views.py:131-269` | `create_session` — pass tools/mcp config to wrapper script | [views.py](https://github.com/ravi-hq/fairy/blob/ecd660c/src/fairy/views.py) |
| `src/fairy/runtimes.py:48-81` | `RuntimeConfig` — may need to extend for MCP config format | [runtimes.py](https://github.com/ravi-hq/fairy/blob/ecd660c/src/fairy/runtimes.py) |

## Architecture Insights

### Data Model

Add two JSON fields to `Agent` and `AgentVersion`:

```python
tools = models.JSONField(default=list, blank=True)       # [{type: "agent_toolset_20260401", ...}, ...]
mcp_servers = models.JSONField(default=list, blank=True)  # [{type: "url", name: "...", url: "..."}]
```

### MCP Auth Strategy

For v1, MCP server auth tokens should be passed via environment variables in the session's environment config, NOT stored on the agent. This mirrors Anthropic's vault pattern (auth separate from agent definition):

1. Agent's `mcp_servers` stores: `[{type: "url", name: "github", url: "https://..."}]` — no secrets
2. Environment's `env_vars` stores: `{"GITHUB_MCP_TOKEN": "ghp_..."}` — encrypted at rest
3. Wrapper script references env vars in the MCP config file generation

### Wrapper Script MCP Config Generation

`sprites_exec.py` needs a new function per runtime that generates the appropriate MCP config:

- **Claude**: Write `/tmp/mcp.json`, add `--mcp-config /tmp/mcp.json --strict-mcp-config` to command
- **Codex**: Write `~/.codex/config.toml` with `[mcp_servers.*]` sections
- **Gemini**: Write `~/.gemini/settings.json` with `mcpServers` object

Each generator translates from fairy's normalized `mcp_servers` format to the runtime-specific format.

### Tool Configuration Mapping

| fairy `tools` type | Claude CLI | Codex CLI | Gemini CLI |
|---------------------|-----------|-----------|------------|
| `agent_toolset_20260401` | `--tools` flag to restrict built-ins | Default tools | Default tools |
| `mcp_toolset` | `--mcp-config` (references server) | `config.toml` MCP section | `settings.json` MCP section |
| `custom` | Not supported in v1 | Not supported in v1 | Not supported in v1 |

### What NOT to build in v1

- Custom tool callback loop (requires real-time bidirectional communication)
- Permission policy enforcement (`always_ask` requires pausing execution for user input)
- Vault/credential management API (use env vars in Environment instead)
- MCP server health checking or validation at agent creation time
- Tool-level enable/disable for Codex and Gemini (Claude only via `--tools` flag)

## Related Research

- `thoughts/research/2026-04-16-environment-model.md` — Environment model patterns (versioning, archiving) that tools/MCP fields will follow
- `thoughts/plans/2026-04-16-environment-model.md` — Implementation plan template for the same patterns

## Open Questions

1. **Should `mcp_servers` support `stdio` type?** — Anthropic only documents `url` (HTTP) type, but all three CLIs support stdio servers. For fairy, HTTP servers are the natural fit (the container connects to external services), but stdio servers (running a local process) might be useful for certain tools.

2. **How to handle MCP auth tokens?** — The recommended approach (env vars in Environment) works but means users must create/update an Environment to add MCP auth. An alternative is accepting `mcp_auth` at session creation time (like Anthropic's `vault_ids`), but this adds API surface.

3. **Should tools be session-scoped or agent-scoped only?** — Anthropic allows tools on both agents and sessions. fairy currently only passes tools from the agent. Adding `tools` to the session creation request would allow per-session tool customization.

4. **How to validate MCP server names in `mcp_toolset` reference `mcp_servers`?** — Should fairy validate at agent creation time that `mcp_toolset.mcp_server_name` matches an entry in `mcp_servers`? Anthropic does this server-side.
