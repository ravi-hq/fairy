---
date: 2026-04-16T17:48:00-07:00
researcher: Claude Code (team-research skill)
git_commit: 9ea0965e5112cea2cee4fbb1ac8d18ad2b9eb833
branch: add-e2e-tests
repository: ravi-hq/fairy
topic: "E2E test suite verifying coding agents respect Agent.tools"
tags: [research, team-research, e2e, tools, claude-cli, codex, gemini, tool-enforcement]
status: complete
method: agent-team
team_size: 4
tracks: [claude-cli, codex-cli, gemini-cli, synthesis]
last_updated: 2026-04-16
last_updated_by: Claude Code
---

# Research: E2E test suite verifying agent tools are respected by coding agents

**Date**: 2026-04-16
**Researcher**: Claude Code (team-research)
**Git Commit**: [`9ea0965`](https://github.com/ravi-hq/fairy/commit/9ea0965e5112cea2cee4fbb1ac8d18ad2b9eb833)
**Branch**: `add-e2e-tests`
**Repository**: ravi-hq/fairy
**Method**: Agent team (4 specialist researchers)

## Research Question

Build an e2e test suite that verifies agent tools are respected by the coding agents (claude, claude-oauth, codex, gemini). `Agent.tools` on Fairy is modeled on Anthropic's Managed Agents `agent_toolset_20260401` spec; each runtime needs its own translation, and tests must prove the translation actually constrains the running agent.

## Summary

Fairy stores and validates `Agent.tools` (in `src/fairy/models.py:222` and `src/fairy/views.py:468`), but **does not wire it through to any runtime CLI**. `sprites_exec.py` currently only translates `mcp_servers`, not `tools`. To build a meaningful e2e test suite, two things must happen together: (1) a new wiring layer in `sprites_exec.py` that translates each Managed-Agents canonical name to runtime-specific flags/config, and (2) a matrix test module that spawns real sessions and inspects their stream output for tool invocations.

Each runtime has a different enforcement surface:
- **Claude CLI**: clean CLI flags — `--tools "Bash,Read"` (allowlist) or `--disallowedTools "WebFetch"` (denylist). PascalCase names. Restrictions don't persist on `--continue` — must re-pass.
- **Codex CLI**: coarse-grained only — `sandbox_mode` (read-only / workspace-write / danger-full-access) plus per-MCP `enabled_tools`. No individual on/off for bash/read/write/edit/glob/grep. No `web_fetch` at all. Six of the eight canonical tools are **hard gaps** for codex.
- **Gemini CLI**: no usable CLI flag — must write `~/.gemini/policies/*.toml` Policy Engine files OR `~/.gemini/settings.json` `tools.core` allowlist before invocation. Managed-Agents `write` maps to two Gemini tools (`write_file` + `replace`) — both must be denied. `tools.exclude` is deprecated; use Policy Engine.

The e2e matrix is **4 runtimes × 8 tools × 2 polarities = 64 sessions**, with ~12 codex combinations marked `xfail`. Net ~60 real sessions per full run at ~$0.35–0.85 on cheapest-model configuration (haiku-4-5 / o4-mini / gemini-2.5-flash).

## Research Tracks

### Track 1: Claude CLI tool enforcement
**Researcher**: claude-cli-researcher
**Scope**: `claude` / `claude-oauth` runtime CLI flags, stream-json event shapes, MCP integration

#### Findings:

1. **`--tools` restricts availability** — `--tools "Bash,Read,Edit"` limits Claude to only those built-in tools. `--tools ""` disables ALL. This is the availability-restriction flag. ([cli-reference](https://code.claude.com/docs/en/cli-reference))
2. **`--allowedTools` ≠ restriction** — `--allowedTools "Bash,Read"` auto-approves those tools without restricting; docs explicitly say "To restrict which tools are available, use `--tools` instead." ([cli-reference](https://code.claude.com/docs/en/cli-reference))
3. **`--disallowedTools` removes from context** — `--disallowedTools "WebFetch,WebSearch"` strips specific tools from the model's toolset. ([cli-reference](https://code.claude.com/docs/en/cli-reference))
4. **`--permission-mode dontAsk`** — Auto-denies anything not in `permissions.allow`; useful for lockdown CI runs. ([cli-reference](https://code.claude.com/docs/en/cli-reference))
5. **`settings.json` `permissions.allow` / `permissions.deny`** — Persistent rules, deny takes absolute priority; managed settings win over CLI flags. ([permissions](https://code.claude.com/docs/en/permissions))
6. **Tool names are PascalCase** — `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`. Managed Agents spec is snake_case — translation layer required. ([tools-reference](https://code.claude.com/docs/en/tools-reference))
7. **MCP naming: `mcp__<server>__<tool>`** — `--strict-mcp-config` only restricts which MCP servers load from `--mcp-config`; does NOT affect `--allowedTools`/`--disallowedTools`. ([permissions#mcp](https://code.claude.com/docs/en/permissions))
8. **No structured tool-denial event** — When `--disallowedTools` or `--tools ""` is used, tool is removed from model context before session starts. No `tool_denied` event exists. Runtime `permissions.deny` in `--print` mode → non-zero exit + stderr, not a JSON event. ([headless](https://code.claude.com/docs/en/headless))
9. **`--continue` does not persist restrictions** — Each invocation must re-pass `--tools` / `--disallowedTools`. In `--print` mode, a disallowed-tool attempt aborts with non-zero exit. ([headless](https://code.claude.com/docs/en/headless))
10. **`claude-oauth` = claude for enforcement** — Only difference is env var (`CLAUDE_CODE_OAUTH_TOKEN` vs `ANTHROPIC_API_KEY`). CLI, flags, stream format identical. ([cli-reference](https://code.claude.com/docs/en/cli-reference))
11. **`--bare` mode** — Skips hooks/skills/plugins/MCP/CLAUDE.md auto-discovery. Defaults to Bash + read + edit only. Useful for reproducible CI. ([headless](https://code.claude.com/docs/en/headless))
12. **Fairy wiring gap confirmed** — `runtimes.py:59` emits `claude --print --verbose --output-format stream-json -p "$PROMPT"` with no `--tools` or `--disallowedTools`. `Agent.tools` is entirely unwired.

#### Managed-Agents → Claude CLI mapping

| Managed Agents name | Claude CLI name | Disable recipe |
|---------------------|-----------------|----------------|
| `bash` | `Bash` | `--disallowedTools "Bash"` |
| `read` | `Read` | `--disallowedTools "Read"` |
| `write` | `Write` | `--disallowedTools "Write"` |
| `edit` | `Edit` | `--disallowedTools "Edit"` |
| `glob` | `Glob` | `--disallowedTools "Glob"` |
| `grep` | `Grep` | `--disallowedTools "Grep"` |
| `web_fetch` | `WebFetch` | `--disallowedTools "WebFetch"` |
| `web_search` | `WebSearch` | `--disallowedTools "WebSearch"` |
| `mcp_toolset` `server_x.tool_y` | `mcp__server_x__tool_y` | `--disallowedTools "mcp__server_x__tool_y"` |
| `mcp_toolset` `server_x` (all) | `mcp__server_x` | `--disallowedTools "mcp__server_x"` |
| `custom` | No direct analog | Only via MCP server |
| disable ALL | n/a | `--tools ""` |

Recipes:
- **Allow-list (`default_config.enabled=false`)**: compute enabled set → `--tools "Bash,Read"`
- **Deny-list (`default_config.enabled=true`)**: compute disabled set → `--disallowedTools "WebFetch,WebSearch"`

#### Stream-json event shapes

```json
// System init (first event)
{"type":"system","subtype":"init","session_id":"...","tools":["Read","Edit","Bash"],"mcp_servers":[],"plugins":[],"model":"claude-sonnet-4-6"}

// Tool use (inside an assistant event)
{"type":"assistant","message":{"id":"msg_...","role":"assistant","model":"...","content":[{"type":"tool_use","id":"toolu_...","name":"Write","input":{"file_path":"...","content":"..."}}],"stop_reason":"tool_use","usage":{...}},"session_id":"..."}

// Tool result (inside a user event)
{"type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_...","type":"tool_result","content":"File created successfully at: /path/to/file.txt"}]},"session_id":"..."}

// Tool result error
{"type":"user","message":{"role":"user","content":[{"tool_use_id":"toolu_...","type":"tool_result","content":"Error: Permission denied","is_error":true}]},"session_id":"..."}

// Final result
{"type":"result","subtype":"success","result":"...","session_id":"...","total_cost_usd":0.001}
```

Assertion: parse stream, find `type=="assistant"` events, check `message.content[]` for `type=="tool_use"` with matching `name`.

### Track 2: Codex CLI tool enforcement
**Researcher**: codex-researcher-v2
**Scope**: `codex` runtime CLI flags, config.toml keys, JSONL event shapes, sandbox + approval mechanics

#### Findings:

1. **`--full-auto`** — Preset: `workspace-write` sandbox + `on-request` approvals. Fairy's current mode. Does NOT disable tools. ([cli/reference](https://developers.openai.com/codex/cli/reference))
2. **`--ask-for-approval`** — `untrusted | on-request | never`. `never` = fully automated, no pauses. ([cli/reference](https://developers.openai.com/codex/cli/reference))
3. **`--sandbox`** — `read-only | workspace-write | danger-full-access`. Filesystem + network scope control. ([cli/reference](https://developers.openai.com/codex/cli/reference))
4. **`--dangerously-bypass-approvals-and-sandbox` / `--yolo`** — Bypasses everything. Not for Fairy.
5. **`--config key=value`** — Inline config override. Per-invocation only; NOT persisted on resume. ([cli/reference](https://developers.openai.com/codex/cli/reference))
6. **`approval_policy` config key** — `untrusted` / `on-request` / `never` / `granular`. ([config-reference](https://developers.openai.com/codex/config-reference))
7. **`sandbox_mode` config key** — `read-only` / `workspace-write` / `danger-full-access`. ([config-reference](https://developers.openai.com/codex/config-reference))
8. **`web_search` top-level config key** — `"cached"` | `"live"` | `"disabled"`. The ONLY built-in tool with a dedicated on/off switch. ([config-basic](https://developers.openai.com/codex/config-basic))
9. **Codex built-in tools** — Just two: `shell` (all bash/command execution, internally sometimes called `Bash`) and `web_search`. No separate `read`/`write`/`edit`/`glob`/`grep` — everything file-related flows through `shell`. No `web_fetch`. ([cli/features](https://developers.openai.com/codex/cli/features))
10. **Starlark execution policy** — `~/.codex/rules/*.rules` in Starlark. `prefix_rule()` matches shell command prefixes with `allow`/`prompt`/`forbidden`. Most restrictive wins. ([exec-policy](https://developers.openai.com/codex/exec-policy))
11. **MCP per-server keys** — `enabled` (bool), `enabled_tools` (allow-list array), `disabled_tools` (deny-list array, applied after allow-list), `required`, `default_tools_approval_mode`. Per-tool: `[mcp_servers.<id>.tools.<name>] enabled=false`. ([config-sample](https://developers.openai.com/codex/config-sample))
12. **Resume doesn't persist CLI flags** — `codex exec resume --last` does NOT carry `--sandbox`/`--ask-for-approval`/`--config` from the prior invocation. Config.toml values DO apply fresh. ([cli/reference](https://developers.openai.com/codex/cli/reference))
13. **JSONL event types** — Top-level: `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`, `error`. Item subtypes: `agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`, `web_search`, `todo_list`, `error`, `unknown`.
14. **PostToolUse hook** — Fires after Bash; only supports Bash tool. Hook can block via `{"decision":"block"}`.
15. **`requirements.toml`** — Admin/org layer; can forbid values like `approval_policy="never"`.

#### Managed-Agents → Codex mapping

| MA name | Codex mechanism | Recipe | GAP? |
|---|---|---|---|
| `bash` | `shell` built-in | Sandbox + Starlark prefix rules; no per-tool on/off | PARTIAL |
| `read` | Via `shell` only | `sandbox_mode=read-only` prevents writes but not reads; cannot disable reads without disabling shell | **GAP** |
| `write` | Via `shell` only | `sandbox_mode=read-only` for blunt-instrument write block | PARTIAL |
| `edit` | Via `shell` only | Same as write | **GAP** |
| `glob` | Via `shell` only | No independent disable | **GAP** |
| `grep` | Via `shell` only | No independent disable | **GAP** |
| `web_fetch` | No equivalent | MCP or shell `curl` workaround only | **GAP** |
| `web_search` | `web_search` built-in | `web_search="disabled"` in config.toml, or `--config web_search=disabled` | MATCH |
| `mcp_toolset` | `[mcp_servers.<id>]` | `enabled_tools`/`disabled_tools`, per-tool `enabled=false` | MATCH |
| `custom` | No equivalent | MCP server workaround | **GAP** |

#### JSONL event shapes

```json
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}

// Command execution (bash/shell)
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"bash -lc ls","status":"completed","output":"docs\nsdk\n","exit_code":0}}

// MCP tool call
{"type":"item.started","item":{"id":"item_2","type":"mcp_tool_call","server":"<name>","tool":"<tool>","input":{...},"status":"in_progress"}}

// Web search
{"type":"item.completed","item":{"id":"item_4","type":"web_search","query":"...","results":[...],"status":"completed"}}

// Agent message
{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"..."}}

{"type":"turn.completed","usage":{"input_tokens":...,"cached_input_tokens":...,"output_tokens":...}}

// Tool denial: NO dedicated event type. Surfaces as turn.failed or item with status:"denied".
```

#### Codex-specific gaps (xfail candidates)

- **GAP-1**: No separate `read` tool — collapses into `shell`
- **GAP-2**: No per-tool `write` disable — only coarse `sandbox_mode`
- **GAP-3**: No separate `edit` tool
- **GAP-4**: No separate `glob` tool
- **GAP-5**: No separate `grep` tool
- **GAP-6**: No `web_fetch` built-in at all
- **GAP-7**: `bash` not discrete — cannot disable shell without breaking the agent
- **GAP-8**: No `custom` tool abstraction
- **GAP-9**: No structured denial event shape
- **GAP-10**: Resume doesn't persist `--config` overrides
- **GAP-11**: `enabled_tools` / `disabled_tools` config-file-only (no CLI flag)
- **GAP-12**: `approval_policy="never"` vs `--full-auto` interaction unclear

### Track 3: Gemini CLI tool enforcement
**Researcher**: gemini-cli-researcher
**Scope**: `gemini` runtime settings.json, Policy Engine TOML, stream-json event shapes

#### Findings:

1. **`settings.json` `tools` key** — `tools.core` (allowlist: if set, ONLY listed tools available; applies to ALL built-ins, not just shell) and `tools.exclude` (blocklist, DEPRECATED). Per-command shell filter: `"tools.core": ["run_shell_command(git)"]` allows only git. ([shell.md#command-restrictions](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/shell.md#command-restrictions))
2. **CLI flags are effectively useless** — `--allowed-tools` is DEPRECATED ("Use the Policy Engine instead"). No `--denied-tools`, `--tools`, or `--disable-tools`. `--allowed-mcp-server-names` restricts which MCP servers start. `--approval-mode` controls confirmation globally. `--sandbox/-s` does filesystem/network isolation, not tool-name restriction. ([cli-reference](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/cli-reference.md))
3. **Policy Engine (primary mechanism)** — TOML files at `~/.gemini/policies/*.toml` are the preferred mechanism. `deny` rule without `argsPattern` excludes the tool from model context entirely. Schema supports `toolName` (string/array; wildcards `*`, `mcp_*`, `mcp_server_*`), `mcpName`, `commandPrefix`/`commandRegex`, `argsPattern`, `decision` (allow/deny/ask_user), `priority` (0-999, tiered: User=4, Admin=5), `interactive` (true=interactive only, false=headless only — critical for Fairy), `modes`, `denyMessage`. ([policy-engine](https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/policy-engine.md))
4. **Built-in tool taxonomy**:

| Canonical | Display | Kind |
|---|---|---|
| `run_shell_command` | Shell | Execute |
| `read_file` | ReadFile | Read |
| `read_many_files` | ReadManyFiles | Read |
| `write_file` | WriteFile | Edit |
| `replace` | EditFile | Edit |
| `glob` | FindFiles | Search |
| `grep_search` | SearchText | Search |
| `list_directory` | ReadFolder | Read |
| `web_fetch` | WebFetch | Fetch |
| `google_web_search` | GoogleSearch | Search |
| `ask_user`, `write_todos`, `save_memory`, etc. | | Interact/Memory |
| `list_mcp_resources`, `read_mcp_resource` | | MCP |

5. **`--resume` persists restrictions** — Policy files and `settings.json` re-loaded on every invocation. No way to resume a session with fewer restrictions than current config.
6. **MCP + `trust:true`** — MCP tools have per-server `includeTools`/`excludeTools` arrays. Policy Engine's `mcpName` / `mcp_*` DO apply to MCP tools. `trust:true` bypasses `ask_user` → `allow` only; does NOT bypass `deny`. In headless mode, `ask_user` already → `deny` — so `trust:true` has no extra effect.
7. **Deny-all-then-allowlist** — `"tools.core": []` disables all built-ins. Or use Policy Engine low-priority `toolName="*" decision="deny"` + higher-priority allow rules. No single boolean equivalent to Claude's `default_config.enabled:false`.

#### Managed-Agents → Gemini mapping

| MA name | Gemini canonical | Disable recipe (TOML) | GAP? |
|---|---|---|---|
| `bash` | `run_shell_command` | `toolName="run_shell_command" decision="deny"` | No |
| `read` | `read_file` | `toolName="read_file" decision="deny"` | Partial (`read_many_files`, `list_directory` separate) |
| `write` | `write_file` + `replace` | `toolName=["write_file","replace"] decision="deny"` | **GAP** — maps to TWO tools |
| `edit` | `replace` | `toolName="replace" decision="deny"` | No |
| `glob` | `glob` | `toolName="glob" decision="deny"` | No |
| `grep` | `grep_search` | `toolName="grep_search" decision="deny"` | No |
| `web_fetch` | `web_fetch` | `toolName="web_fetch" decision="deny"` | No |
| `web_search` | `google_web_search` | `toolName="google_web_search" decision="deny"` | Partial (API grounding; deny in headless unconfirmed) |
| `custom` | N/A | `mcpName="*" decision="deny"` | **GAP** |
| `mcp_toolset` | per-server MCP | `mcpName="<server>" decision="deny"` | No |

#### TOML deny-all + allowlist example

```toml
[[rule]]
toolName = "*"
decision = "deny"
priority = 1

[[rule]]
toolName = ["read_file", "glob", "grep_search"]
decision = "allow"
priority = 100
```

#### Stream-json event shapes

```jsonc
{"type":"init","timestamp":"...","session_id":"...","model":"gemini-2.5-pro"}
{"type":"message","timestamp":"...","role":"assistant","content":"...","delta":true}

// Tool use
{"type":"tool_use","timestamp":"...","tool_name":"run_shell_command","tool_id":"call_xyz","parameters":{"command":"echo hello"}}

// Tool result — success
{"type":"tool_result","timestamp":"...","tool_id":"call_xyz","status":"success","output":"hello\n"}

// Tool result — denied (no dedicated denial event)
{"type":"tool_result","timestamp":"...","tool_id":"call_xyz","status":"error","error":{"type":"PolicyDenial","message":"Tool execution denied by policy"}}

{"type":"error","timestamp":"...","severity":"warning","message":"..."}

{"type":"result","timestamp":"...","status":"success","stats":{...}}
```

Assertion: `event.type == "tool_use" && event.tool_name == <expected>`; denial = `tool_result.status == "error"` + `error.message` contains "denied by policy".

#### Gemini-specific gaps

- **GAP-1**: No usable CLI flag for per-invocation restriction — all restriction is file-based
- **GAP-2**: `tools.exclude` is deprecated
- **GAP-3**: MA `write` needs TWO Gemini tools denied (`write_file` + `replace`)
- **GAP-4**: No dedicated denial event — must string-match error message
- **GAP-5**: Workspace-level policies (`.gemini/policies/*.toml` in project dir) broken (issue #18186); must use `~/.gemini/policies/`
- **GAP-6**: Config files must be pre-written before `gemini` invocation (no inline flag option)
- **GAP-7**: `google_web_search` uses Gemini API grounding — policy deny in headless unconfirmed

### Track 4: Synthesis — wiring plan + e2e test design
**Researcher**: synthesis-v2
**Scope**: map findings back to Fairy source code; produce wiring plan, test matrix, prompt playbook, and test module skeleton

#### Part A — Fairy Wiring Plan

**Files requiring changes:**
- `src/fairy/sprites_exec.py` — add `_build_tool_flags_and_files()` called from `build_wrapper_script()`
- `src/fairy/views.py` — pass `agent_obj.tools` into `build_wrapper_script()` at `create_session` (line 225) AND `send_prompt` continue path

**Proposed function signature:**
```python
def _build_tool_flags(
    runtime: str,
    tools: list[dict],
) -> dict:
    """
    Returns:
        cli_flags: str — extra flags to append to the exec line
        files: dict[str, str] — {absolute_path: file_content} written before exec
    """
```

Dispatches to `_tool_flags_claude(tools)`, `_tool_files_codex(tools)`, `_tool_files_gemini(tools)`. The `files` dict entries are emitted as heredoc blocks in the wrapper script (same pattern as `_build_mcp_claude`).

**Per-runtime translation:**

For **claude / claude-oauth**: parse `agent_toolset_20260401` entries.
- `default_config.enabled=False`, no per-tool overrides → `--tools ""`
- `default_config.enabled=True`, some disabled → `--disallowedTools "WebFetch,Edit"` (PascalCase)
- `default_config.enabled=False`, some enabled → `--tools "Bash,Read"`
- Mixed (default=True, some disabled) → prefer `--disallowedTools`
- `mcp_toolset`: already handled via `--mcp-config`
- `custom`: GAP — ignored at CLI layer

For **codex**: extend `_build_mcp_codex()` to also write top-level `sandbox_mode` and `web_search` config keys.
- If `web_search` disabled → `web_search = "disabled"`
- If `write` and `edit` both disabled → `sandbox_mode = "read-only"`
- Otherwise `workspace-write`
- Per-MCP `enabled_tools`/`disabled_tools` written under `[mcp_servers.<name>]`

For **gemini**: extend `_build_mcp_gemini()` to also write `~/.gemini/policies/fairy.toml`.
- Convert each canonical name to Gemini's canonical (with `write` → both `write_file` and `replace`)
- Emit `[[rule]] toolName=... decision="deny" interactive=false` entries

#### Part B — Existing e2e patterns (must reuse)

From `tests/e2e/conftest.py`:
- `FairyClient` HTTP wrapper — methods: `create_agent`, `create_session`, `collect_stream`, `run_session`, `stream_session_raw`, `wait_for_session`
- `RUNTIME_MODELS` — `{"claude":"claude-haiku-4-5","codex":"o4-mini","gemini":"gemini-2.5-flash","claude-oauth":"claude-haiku-4-5"}`
- `create_agent` / `create_environment` / `create_session` factory fixtures with auto-cleanup
- `api` session-scoped `FairyClient` fixture
- `e2e_runtimes` fixture — parsed `E2E_RUNTIMES`
- `stream_stdout(events)`, `stream_all_output(events)` — helpers
- `_unique(prefix)` — name helper

From `tests/e2e/test_sessions.py`:
- `pytestmark = pytest.mark.slow` — module-level convention
- Class-scoped `runtime` fixture parametrized on `RUNTIME_MODELS.keys()` with `e2e_runtimes` skip
- `_create_throwaway_agent()` / `_start_throwaway_session()` — for class-scoped fixtures
- `api.run_session(sid)` — returns `(final_status_dict, events_list)`

#### Part C — Test matrix (runtime × tool × polarity)

| Runtime | bash | read | write | edit | glob | grep | web_fetch | web_search |
|---------|------|------|-------|------|------|------|-----------|------------|
| **claude** | A+D | A+D | A+D | A+D | A+D | A+D | A+D | A+D |
| **claude-oauth** | A+D | A+D | A+D | A+D | A+D | A+D | A+D | A+D |
| **codex** | xfail | xfail | A+D (sandbox) | xfail | xfail | xfail | xfail | A+D (config) |
| **gemini** | A+D | A+D | A+D | A+D | A+D | A+D | A+D | A+D |

Codex xfail reasons (from `RUNTIME_GAP_MATRIX` in skeleton):
- `bash`, `read`, `edit`, `glob`, `grep` — no per-tool flag
- `web_fetch` — no codex equivalent at all

#### Part D — Prompt playbook

| Canonical | Prompt | claude | codex | gemini | Signal |
|---|---|---|---|---|---|
| `bash` | "Run the shell command `echo TOOL_SIGNAL_BASH` using your shell/bash tool" | `Bash` | `shell` | `run_shell_command` | tool_use.name |
| `read` | "Use your file-read tool to read /etc/hostname" | `Read` | n/a | `read_file` | tool_use.name |
| `write` | "Use your file-write tool to create /tmp/fairy_tool_test_write.txt with 'TOOL_SIGNAL_WRITE'" | `Write` | n/a | `write_file` | tool_use.name |
| `edit` | "Use your file-edit/replace tool to change 'OLD_CONTENT' to 'NEW_CONTENT' in /tmp/fairy_tool_test_edit.txt" | `Edit` | n/a | `replace` | tool_use.name |
| `glob` | "Use your glob tool to list files matching /tmp/*.txt (not shell find/ls)" | `Glob` | n/a | `glob` | tool_use.name |
| `grep` | "Use your grep tool to search 'root' in /etc/passwd (not shell)" | `Grep` | n/a | `grep_search` | tool_use.name |
| `web_fetch` | "Use your web fetch tool to fetch https://httpbin.org/get" | `WebFetch` | n/a | `web_fetch` | tool_use.name |
| `web_search` | "Use your web search tool to search 'current UTC date'" | `WebSearch` | `web_search` | `google_web_search` | tool_use.name |

**Allow assertion**: `_tool_was_invoked(events, runtime, tool) == True`
**Deny assertion**: `_tool_was_invoked(events, runtime, tool) == False`

#### Part E — Cost & bucketing

- Every test: `@pytest.mark.slow` (existing)
- Add `@pytest.mark.tool_matrix` marker for this module
- Register in `pyproject.toml`:
  ```toml
  [tool.pytest.ini_options]
  markers = [
      "slow: spawns real agent sessions",
      "tool_matrix: tests tool enforcement across runtimes",
  ]
  ```
- New `make test-e2e-tools` target
- `make test-e2e-fast` already excludes `@slow`, so `tool_matrix` tests auto-excluded

**Cost estimate**:
- Matrix: 4 runtimes × 8 tools × 2 polarities = 64 sessions + 4 TestDefaultEnabled + 4 TestDenyAll = 72 total
- Minus ~12 codex xfails = ~60 actual sessions run
- claude-haiku-4-5: ~$0.003–0.01/session × 20 ≈ $0.10–0.20
- o4-mini: ~$0.01–0.03/session × 16 ≈ $0.20–0.50
- gemini-2.5-flash: ~$0.002–0.008/session × 20 ≈ $0.05–0.15
- **Total per full run: ~$0.35–0.85**

#### Part F — `test_agent_tools.py` skeleton

Key structure:

```python
"""E2E tests verifying that Agent.tools field is enforced by each runtime CLI."""

from __future__ import annotations
import json
import pytest
from tests.e2e.conftest import RUNTIME_MODELS, FairyClient, _unique, stream_all_output

pytestmark = [pytest.mark.slow, pytest.mark.tool_matrix]

CANONICAL_TOOLS = ["bash", "read", "write", "edit", "glob", "grep", "web_fetch", "web_search"]

RUNTIME_TOOL_NAMES: dict[str, dict[str, str | None]] = {
    "claude": {"bash":"Bash","read":"Read","write":"Write","edit":"Edit","glob":"Glob","grep":"Grep","web_fetch":"WebFetch","web_search":"WebSearch"},
    "claude-oauth": {"bash":"Bash","read":"Read","write":"Write","edit":"Edit","glob":"Glob","grep":"Grep","web_fetch":"WebFetch","web_search":"WebSearch"},
    "codex": {"bash":"shell","read":None,"write":"write_file","edit":None,"glob":None,"grep":None,"web_fetch":None,"web_search":"web_search"},
    "gemini": {"bash":"run_shell_command","read":"read_file","write":"write_file","edit":"replace","glob":"glob","grep":"grep_search","web_fetch":"web_fetch","web_search":"google_web_search"},
}

RUNTIME_GAP_MATRIX: dict[tuple[str, str], str | None] = {
    ("codex", "bash"): "codex has no per-tool bash disable; enforced at sandbox_mode level only",
    ("codex", "read"): "codex has no per-tool read disable",
    ("codex", "edit"): "codex edit maps to sandbox_mode only, not individually toggleable",
    ("codex", "glob"): "codex has no per-tool glob disable",
    ("codex", "grep"): "codex has no per-tool grep disable",
    ("codex", "web_fetch"): "codex has no web_fetch equivalent tool",
}

@pytest.fixture(scope="session", params=list(RUNTIME_MODELS.keys()))
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param

def _xfail_if_gap(runtime: str, tool: str) -> None:
    reason = RUNTIME_GAP_MATRIX.get((runtime, tool))
    if reason:
        pytest.xfail(reason)

def _prompt_for(tool: str) -> str: ...  # see full skeleton

def _parse_claude_events(raw_events: list[dict]) -> list[str]:
    """Find tool_use blocks inside assistant events."""
    ...

def _parse_gemini_events(raw_events: list[dict]) -> list[str]:
    """Find events where type=='tool_use' and extract tool_name."""
    ...

def _parse_codex_events(raw_events: list[dict]) -> list[str]:
    """Find item.started events with type=='command_execution' or 'mcp_tool_call' or 'web_search'."""
    ...

def _tool_was_invoked(events, runtime, tool) -> bool:
    runtime_tool_name = RUNTIME_TOOL_NAMES.get(runtime, {}).get(tool)
    if runtime_tool_name is None:
        return False
    if runtime in ("claude", "claude-oauth"):
        return runtime_tool_name in _parse_claude_events(events)
    elif runtime == "gemini":
        return runtime_tool_name in _parse_gemini_events(events)
    elif runtime == "codex":
        return runtime_tool_name in _parse_codex_events(events)
    return False

def _make_allow_toolset(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": False},
        "configs": [{"name": tool, "enabled": True}],
    }]

def _make_deny_toolset(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True},
        "configs": [{"name": tool, "enabled": False}],
    }]

def _make_deny_all_toolset() -> list[dict]:
    return [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]


class TestToolAllow:
    @pytest.mark.parametrize("tool", CANONICAL_TOOLS)
    def test_allow_tool_is_invoked(self, api, create_agent, create_session, runtime, tool):
        _xfail_if_gap(runtime, tool)
        agent = create_agent(name=_unique(f"e2e-allow-{runtime}-{tool}"),
                             model=RUNTIME_MODELS[runtime], runtime=runtime,
                             tools=_make_allow_toolset(tool))
        session = create_session(agent_id=agent["id"], prompt=_prompt_for(tool), timeout=120)
        final, events = api.run_session(session["id"])
        assert final["status"] == "completed"
        assert _tool_was_invoked(events, runtime, tool)


class TestToolDeny:
    @pytest.mark.parametrize("tool", CANONICAL_TOOLS)
    def test_deny_tool_not_invoked(self, api, create_agent, create_session, runtime, tool):
        _xfail_if_gap(runtime, tool)
        agent = create_agent(..., tools=_make_deny_toolset(tool))
        session = create_session(agent_id=agent["id"], prompt=_prompt_for(tool), timeout=120)
        final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, runtime, tool)


class TestDefaultEnabled:
    def test_no_tools_field_session_completes(self, ...): ...
    def test_explicit_all_enabled_session_completes(self, ...): ...


class TestDenyAll:
    def test_deny_all_no_tools_invoked(self, ...):
        # Prompt that would normally trigger bash + file tools
        # Assert no canonical tool (excluding gaps) was invoked
        ...


class TestMcpToolset:
    @pytest.mark.skip(reason="Requires live MCP endpoint via E2E_MCP_URL")
    def test_mcp_tool_invoked(self, ...): ...


class TestCustomTool:
    @pytest.mark.skip(reason="TODO: custom tool enforcement not yet wired in Fairy CLI layer")
    def test_custom_tool_schema_available(self, ...): ...
```

Full skeleton available in the team's synthesis deliverable (preserved in team transcript).

## Cross-Track Discoveries

1. **The wiring gap is symmetric but the enforcement surface isn't** — All three runtimes confirm `Agent.tools` is stored but not translated, yet each runtime requires a completely different enforcement strategy: claude uses CLI flags, gemini needs on-disk TOML policies, codex has coarse sandbox modes plus MCP per-server keys. The `_build_tool_flags()` function must return both `cli_flags: str` AND `files: dict[path, content]` to handle this diversity.

2. **Tool-denial events are universally absent as structured events** — Claude, codex, and gemini all lack a dedicated `tool_denied` stream event type. Denial signals differ: claude removes the tool from the `system/init` `tools[]` array (silent — model never sees it); gemini emits a `tool_result` with `status:"error"` and message "Tool execution denied by policy"; codex surfaces denial as non-zero exit or `turn.failed`. The test helper `_tool_was_invoked()` must therefore not assert on a denial event type — it must check tool *absence* in the stream.

3. **The Managed-Agents spec ergonomics break down for codex** — Six of the eight canonical tool names have no enforceable codex analog. The Managed-Agents API contract becomes an aspirational surface for codex; we can only enforce `web_search`, sandbox-level write restriction, and MCP per-server allowlisting. This is a product-level clarification the Fairy team should document in the README.

4. **MCP tool naming converges on prefix-based schemes across runtimes** — Claude uses `mcp__<server>__<tool>`, gemini uses `mcpName="<server>"` in policies, codex uses `[mcp_servers.<id>.tools.<name>]`. Fairy's validator accepts `mcp_toolset` with `mcp_server_name`; the runtime-specific translation is straightforward per-runtime but the central abstraction is sound.

5. **Resume / multi-turn semantics vary** — Claude `--continue` doesn't persist flag-based restrictions (must re-pass); gemini `--resume` re-reads policy files fresh (restrictions persist by happenstance); codex `exec resume` doesn't persist `--config` overrides either. The Fairy `send_prompt` continue path must pass `agent.tools` through `build_wrapper_script` on every call — not just initial creation.

## Code References

| File | Tracks | Findings | Link |
|------|--------|----------|------|
| `src/fairy/runtimes.py:56-81` | 1, 2, 3, 4 | Where runtime `cmd` / `continue_cmd` templates live — injection point for flags | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/runtimes.py#L56-L81) |
| `src/fairy/sprites_exec.py:259-293` | 4 | `build_wrapper_script` — needs new `tools=` parameter + `_build_tool_flags` companion | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/sprites_exec.py#L259-L293) |
| `src/fairy/sprites_exec.py:120-206` | 4 | `_build_mcp_claude` / `_build_mcp_codex` / `_build_mcp_gemini` — pattern to follow for `_build_tool_files_*` | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/sprites_exec.py#L120-L206) |
| `src/fairy/views.py:224-228` | 4 | `create_session` — call site that currently passes `mcp_servers` but not `tools` | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L224-L228) |
| `src/fairy/views.py:461-510` | 4 | `_validate_tools` / `_validate_mcp_servers` + `AGENT_VERSIONED_FIELDS` | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/src/fairy/views.py#L461-L510) |
| `tests/e2e/conftest.py:20-27,203-229` | 4 | `RUNTIME_MODELS` + `api`/`create_agent`/`create_session` fixtures | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/tests/e2e/conftest.py#L20-L229) |
| `tests/e2e/test_sessions.py:21-47` | 4 | Class-scoped `runtime` parametrization pattern to reuse | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/tests/e2e/test_sessions.py#L21-L47) |
| `tests/e2e/test_agents.py` | 4 | Existing tool-validation tests (API-level, not runtime-level) | [permalink](https://github.com/ravi-hq/fairy/blob/9ea0965/tests/e2e/test_agents.py) |

## Architecture Insights

**The Managed-Agents spec is Fairy's public contract; per-runtime translation is Fairy's responsibility.** Users write `{type: "agent_toolset_20260401", configs: [{name: "web_fetch", enabled: false}]}` once, and Fairy translates that to `--disallowedTools WebFetch` for claude, a TOML policy for gemini, and `web_search="disabled"` for codex's analog. Gaps must be surfaced clearly — either at validation time (reject configurations that can't be enforced on the chosen runtime) or at documentation time (explicit README table of enforceable-per-runtime).

**Recommendation**: introduce a per-runtime "capability matrix" exposed via an admin endpoint or in `runtimes.py`, so the API can warn at agent creation time when a tools configuration is partially-enforceable. This is similar to how `MODEL_RUNTIME_MAP` already constrains which models pair with which runtime.

**Stream-json heterogeneity is the primary test-design risk.** Each runtime's event format is different enough that `_tool_was_invoked` needs three parsers. Keep these parsers small, tested (unit tests with recorded fixtures would help), and in `tests/e2e/` or a sibling helpers module — not in production code.

## Historical Context

- `thoughts/research/2026-04-16-agent-tools-and-mcp.md` — prior research into the `Agent.tools` / `mcp_servers` API schema
- `thoughts/research/2026-04-16-e2e-test-suite.md` — design of the existing e2e suite (patterns this test module will reuse)

## Related Research

- Prior session-execution research: `thoughts/research/2026-04-16-session-based-execution.md`
- Sprites platform: `thoughts/research/2026-04-15-sprites-platform-research.md`, `thoughts/research/2026-04-16-sprites-deep-dive.md`

## Open Questions

1. **Custom tools in non-API runtimes** — The Managed-Agents `custom` tool type declares a tool to a model and expects the *caller* to execute it (client-executed tool use). Claude/codex/gemini CLIs don't expose a mechanism for this; "custom" only flows through MCP. Should Fairy reject `custom` entries at validation time, or silently treat them as a no-op at the CLI layer?
2. **Codex web_fetch workaround** — If a user configures `web_fetch: enabled` on a codex agent, should Fairy transparently substitute an MCP server that provides web_fetch (increases complexity, leaks implementation) or reject the configuration at agent-create time (breaks spec portability)?
3. **Gemini `google_web_search` headless enforcement** — Researcher flagged that denial of `google_web_search` (which uses Gemini API grounding, not a local tool) may not actually take effect in headless mode. Worth an integration test to confirm before shipping.
4. **Multi-turn consistency** — When `POST /sessions/{id}/prompt` is called on an existing session, should the tools be taken from the current agent version, or frozen to the version at session creation? Affects whether tool restrictions can drift mid-conversation.
