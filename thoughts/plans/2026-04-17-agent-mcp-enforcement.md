# Agent MCP Enforcement Implementation Plan

## Overview

Wire Fairy's `mcp_toolset` tool entries through to the coding-agent runtime CLIs so that MCP tools declared as disabled in the Managed-Agents spec are actually not invocable by the running agent, and add a focused e2e test suite that proves the translation works end-to-end.

Today, `Agent.mcp_servers` is correctly wired (each runtime receives the server list + auth headers), but `mcp_toolset` entries — the allow/deny surface for MCP tools — are only partially consulted. Only claude's allowlist path (`default_config.enabled=False`) reads them; claude's denylist, codex, and gemini all ignore `mcp_toolset` entirely. Per-tool filtering via `configs[].name` is validated as optional and never wired anywhere. This is the direct MCP analog of the built-in tool-enforcement gap that commit [`4c3b9cc`](https://github.com/ravi-hq/fairy/commit/4c3b9cc1e5e7871436bdec48c063c76d47db30a0) closed.

This plan closes that gap per-runtime and adds a focused e2e matrix that uses a new Fairy-hosted test MCP endpoint to prove the claim.

## Research Summary

Research conducted by agent team with 5 specialist tracks — full document at `thoughts/research/2026-04-17-mcp-enforcement-e2e.md`.

- **Fairy wiring audit**: `mcp_toolset.mcp_server_name` reaches runtime only via claude `_tool_flags_claude` allowlist path. `configs[].name` + `default_config.enabled` on `mcp_toolset` are stored, returned, ignored.
- **Per-runtime gating**: Claude `--disallowedTools` silently ignores MCP names in `-p` mode (upstream #12863). Claude fix path is file-based `permissions.deny` in `settings.json`. Codex has rich config: `enabled_tools` + `disabled_tools` + `enabled` + `required`. Gemini has two options — Policy Engine single-underscore `mcp_{server}_{tool}` rules, or `excludeTools` in the `mcpServers` settings entry (no naming trap).
- **Test infrastructure**: Sprites have unrestricted outbound network — a Fairy-hosted `/test-mcp` Django view using the `mcp` Python SDK (FastMCP) is the cleanest option. Three deterministic tools: `signal_tool`, `echo`, `dangerous_tool`.
- **Matrix design**: 3 runtimes × 4 scenarios + 1 multi-turn = ~14 sessions/run at haiku/o4-mini/gemini-flash ≈ $0.30–0.70.
- **Managed Agents parity**: vaults deferred (API not GA, Fairy has no vault model). Inline `headers` on `Agent.mcp_servers` are sufficient for this effort. Plaintext headers + returned-in-responses is a separate hardening item.

### Key Discoveries

- MCP tool-call events are parseable on all 3 runtimes: Claude `tool_use` block with `name="mcp__<server>__<tool>"`, Codex `item.type="mcp_tool_call"` with `item.server` + `item.tool`, Gemini `tool_use` with `tool_name="mcp__<server>__<tool>"` (`thoughts/research/2026-04-17-mcp-enforcement-e2e.md`, Track 2).
- Codex `item.type="mcp_tool_call"` parsing is already present at `tests/e2e/test_agent_tools.py:89-92` and can be reused directly.
- `_tool_files_gemini` at `src/fairy/sprites_exec.py:342-392` is NOT the right pattern for MCP gemini deny — Policy Engine uses single-underscore naming and trips on server aliases containing underscores. `excludeTools` inside the `mcpServers` entry (written by `_build_mcp_gemini`) is the chosen path.
- Fairy already supports `stdio`-type MCP servers end-to-end (`src/fairy/views.py:501-523` + all three `_build_mcp_*` writers). This plan uses `type="url"` only since stdio can't test bearer-auth headers.
- `send_prompt` at `src/fairy/views.py:378-395` already re-passes `mcp_servers` + `tools` on every turn — multi-turn persistence is free.
- `RUNTIME_MODELS` at `tests/e2e/conftest.py:20-27` pins cheapest models — same pins that the tool matrix uses.

## Current State Analysis

`Agent.mcp_servers` flows end-to-end. `Agent.tools` with `mcp_toolset` entries flows partially:

- `src/fairy/models.py:222-223` defines `tools` + `mcp_servers` as plain JSONFields.
- `src/fairy/views.py:485-498` (`_validate_tools`) — `mcp_toolset` entries require only `mcp_server_name`; `configs[].name` and `default_config.enabled` are accepted as optional shape but never validated for type/meaning. No validation that `mcp_server_name` references an actual server on the agent.
- `src/fairy/views.py:501-523` (`_validate_mcp_servers`) — url/stdio types, max 20, name uniqueness. Headers accepted as arbitrary dict.
- `src/fairy/views.py:595-613` (`_serialize_agent`) returns full `mcp_servers` including `headers` containing bearer tokens.
- `src/fairy/sprites_exec.py:120-140` (`_build_mcp_claude`) writes `/tmp/mcp.json`.
- `src/fairy/sprites_exec.py:170-210` (`_build_mcp_codex`) writes `~/.codex/config.toml` — only `url` + `bearer_token_env_var` + `required`; no allow/deny keys.
- `src/fairy/sprites_exec.py:213-233` (`_build_mcp_gemini`) writes `~/.gemini/settings.json` — no `excludeTools`/`includeTools`.
- `src/fairy/sprites_exec.py:286-321` (`_tool_flags_claude`) — `mcp_toolset` consulted only in allowlist path (`default_config.enabled=False`); denylist path ignores MCP entirely.
- `tests/test_tools_mcp.py` — unit tests cover MCP config generation and validation, but no MCP deny-path tests because no deny path exists.
- `tests/e2e/test_agent_tools.py` — covers built-in tools only; MCP e2e coverage was explicitly deferred in `thoughts/plans/2026-04-16-agent-tool-enforcement.md:63`.

A user can declare `mcp_toolset: {mcp_server_name: "github", default_config: {enabled: false}}` expecting the github MCP server to be blocked — and Fairy will happily emit a wrapper that lets the agent call every tool on that server. That's the gap.

## Desired End State

- Creating a session from an agent with `mcp_toolset` tool entries produces a wrapper script that actually constrains MCP tool availability per-runtime.
- For **claude/claude-oauth**: deny entries translate to `permissions.deny` rules in a Fairy-written `~/.claude/settings.json` (new writer). Allow entries continue to use `--tools "mcp__<server>"` as today.
- For **codex**: deny entries translate to `enabled_tools`/`disabled_tools`/`enabled` keys on `[mcp_servers.<name>]` blocks in `~/.codex/config.toml`.
- For **gemini**: deny entries translate to `excludeTools` arrays on the `mcpServers` entry in `~/.gemini/settings.json` (extends existing `_build_mcp_gemini`, does NOT touch `_tool_files_gemini` Policy Engine).
- `_validate_tools` at `src/fairy/views.py:485-498` tightens `mcp_toolset` validation: require that `mcp_server_name` references an actual server on the agent (422 otherwise), validate optional `configs[].name` (string) + `configs[].enabled` (bool), validate optional `default_config.enabled` (bool).
- New Django view `/test-mcp` served by `src/fairy/test_mcp.py` implementing MCP Streamable HTTP. Gated behind `settings.DEBUG or settings.TESTING`. Tools: `signal_tool`, `echo`, `dangerous_tool`. Optional bearer check via `MCP_TEST_TOKEN` env.
- E2E test `tests/e2e/test_mcp_enforcement.py` — 3 runtimes × 4 scenarios + 1 multi-turn = ~14 sessions per full run. New marker `mcp_matrix`. New `make test-e2e-mcp` target.
- `README.md` includes a per-runtime MCP-enforcement capability table.

### Verification

- `make test` passes (new unit tests green, existing tests still pass).
- `make test-e2e-mcp E2E_RUNTIMES=claude` passes against a running Fairy — all claude-path MCP tests green.
- `make test-e2e-mcp` with all runtimes runs ~14 sessions, completes under $0.70, all asserted behaviors enforced.
- `make lint` + `make test-e2e-fast` continue to pass unchanged.
- `make test-e2e-tools` (prior tool matrix) continues to pass unchanged.

## What We're NOT Doing

- **Vaults**: no session-level `vault_ids` model, no vault resource, no OAuth+refresh MCP auth. Inline `headers` on `Agent.mcp_servers` only. Tracked in research doc as future work.
- **`Agent.mcp_servers[].headers` encryption**: separate security-hardening ticket. Plaintext at rest + returned-in-responses is a known gap but orthogonal to this enforcement work. Test fixtures use fake sentinel tokens.
- **422 on unenforceable combinations**: a `mcp_toolset` deny against a server that isn't in `mcp_servers[]` is a 422 (name-must-resolve), but all other combinations are accepted silently. Per-tool `configs[].name` entries that don't match any tool the server actually exposes are accepted silently (we can't know what tools a remote server exposes at validation time).
- **Gemini Policy Engine for MCP**: we use `excludeTools` in `mcpServers` settings entry, not Policy Engine rules. Single-underscore Policy Engine naming is a footgun for user-chosen server aliases.
- **Claude `permissions.deny` for built-in tools**: stays on existing `--disallowedTools` CLI flag path. The new `settings.json` writer is scoped to MCP rules only.
- **stdio MCP server in e2e**: URL-type only. stdio can't test bearer-auth headers and adds no coverage we don't already have from HTTP.
- **Stream-event auth failure assertions**: no runtime emits a structured MCP auth-failure event reliably. Auth tests use tool-absence assertions only.

## Implementation Approach

Six phases. Phases 1–4 are strictly sequential (they all touch `sprites_exec.py` or `views.py`). Phase 5 (test MCP server) can overlap with Phase 4. Phase 6 (e2e) depends on 1–5. Unit tests land alongside each runtime phase. E2E tests land only once all runtime paths + test server are wired.

## File Ownership Map

Designed for parallel execution via `team-implement`. Because phases 1–4 serialize on `sprites_exec.py`/`views.py`, all backend work in these phases is a single track.

| Phase | Files | Owner Track | Change Type |
|-------|-------|-------------|-------------|
| 1 | `src/fairy/views.py` | backend | modify — tighten `_validate_tools` for `mcp_toolset` |
| 1 | `src/fairy/sprites_exec.py` | backend | modify — `_build_mcp_*` accept `mcp_toolset_rules`, no behavior yet |
| 1 | `tests/test_tools_mcp.py` | backend | modify — validation tests for new 422 cases |
| 2 | `src/fairy/sprites_exec.py` | backend | modify — codex `enabled`/`enabled_tools`/`disabled_tools` |
| 2 | `tests/test_tools_mcp.py` | backend | modify — codex unit tests |
| 3 | `src/fairy/sprites_exec.py` | backend | modify — gemini `excludeTools` in `_build_mcp_gemini` |
| 3 | `tests/test_tools_mcp.py` | backend | modify — gemini unit tests |
| 4 | `src/fairy/sprites_exec.py` | backend | modify — new `_tool_files_claude_mcp` settings.json writer |
| 4 | `tests/test_tools_mcp.py` | backend | modify — claude unit tests |
| 5 | `src/fairy/test_mcp.py` | tests-infra | create — FastMCP Django view |
| 5 | `src/config/urls.py` | tests-infra | modify — mount `/test-mcp` behind DEBUG/TESTING |
| 5 | `src/config/settings.py` | tests-infra | modify — `TESTING` flag |
| 5 | `pyproject.toml` | tests-infra | modify — add `mcp` to `[project.optional-dependencies].e2e` |
| 6 | `tests/e2e/test_mcp_enforcement.py` | tests | create |
| 6 | `tests/e2e/conftest.py` | tests | modify — `mcp_test_url` fixture |
| 6 | `pyproject.toml` | tests | modify — register `mcp_matrix` marker |
| 6 | `Makefile` | tests | modify — `test-e2e-mcp` target |
| 6 | `README.md` | tests | modify — MCP capability table |

**Conflict-free guarantee**: Phases 1–4 all touch `sprites_exec.py` and are strictly sequential (single backend track). Phases 5 and 6 touch disjoint files and can run in parallel after Phase 4 lands, with Phase 6 depending on Phase 5 for the test server endpoint.

---

## Phase 1: Validator tightening + MCP toolset dispatcher foundation

### Overview

Tighten `_validate_tools` to properly validate `mcp_toolset` structure and require `mcp_server_name` references resolve to an actual server on the agent. Introduce a pure helper `_build_mcp_toolset_rules(tools, mcp_servers) -> dict[str, ToolsetRules]` that the runtime-specific `_build_mcp_*` functions will consume in Phases 2–4. At the end of Phase 1 the rules helper exists but no runtime reads it — only validation behavior changes.

### Changes Required

#### 1. `src/fairy/views.py` — tighten `_validate_tools`

Extend `_validate_tools` to:
- Require `configs` is a list of dicts if present; each dict has optional `name: str` + `enabled: bool`.
- Require `default_config` is a dict if present, with optional `enabled: bool`.
- Require `mcp_server_name` resolves to a server in the agent's `mcp_servers[]`. 422 otherwise with `detail: "mcp_toolset references unknown server: <name>"`.

Touch both CREATE and UPDATE paths (the existing `_validate_tools` is shared).

#### 2. `src/fairy/sprites_exec.py` — add `_build_mcp_toolset_rules`

Add a new pure helper that converts `tools` + `mcp_servers` into a structured per-server rules dict:

```python
@dataclass(frozen=True)
class McpToolsetRules:
    """Normalized MCP toolset restrictions for a single server."""
    server_enabled: bool  # False → whole server blocked
    default_enabled: bool  # default for tools not listed in configs
    per_tool: dict[str, bool]  # {tool_name: enabled}


def _build_mcp_toolset_rules(
    tools: list[dict],
    mcp_servers: list[McpServerSpec],
) -> dict[str, McpToolsetRules]:
    """
    Returns {server_name: McpToolsetRules} for every server that has
    an mcp_toolset entry. Servers without an mcp_toolset entry are absent
    from the dict (meaning: no restrictions).

    Default semantics:
      - No mcp_toolset for server X → X absent from result → fully allowed
      - mcp_toolset with no default_config → default_enabled=True
      - mcp_toolset with default_config.enabled=False → server_enabled=True, default_enabled=False
      - configs[].enabled overrides default for that specific tool name
    """
    rules: dict[str, McpToolsetRules] = {}
    for t in tools:
        if t.get("type") != "mcp_toolset":
            continue
        name = t.get("mcp_server_name")
        if not name:
            continue
        default_enabled = t.get("default_config", {}).get("enabled", True)
        per_tool = {c["name"]: c.get("enabled", True) for c in t.get("configs", []) if "name" in c}
        rules[name] = McpToolsetRules(
            server_enabled=True,  # presence of entry = server registered
            default_enabled=default_enabled,
            per_tool=per_tool,
        )
    return rules
```

Pass `rules` into each `_build_mcp_<runtime>` via a new parameter, but have each `_build_mcp_*` ignore it for now (Phases 2–4 fill them in).

Extend `_build_mcp_section` and the call site in `build_wrapper_script` to thread `rules` through. No behavior change yet.

#### 3. `tests/test_tools_mcp.py` — validation test cases

Add test cases to `TestAgentMcpValidation` / `TestCreateAgentWithTools`:

- `test_mcp_toolset_unknown_server_rejected_with_422` — `mcp_toolset` with `mcp_server_name` not in `mcp_servers[]`
- `test_mcp_toolset_configs_non_list_rejected` — `configs: "not-a-list"`
- `test_mcp_toolset_configs_name_non_string_rejected` — `configs: [{"name": 42}]`
- `test_mcp_toolset_configs_enabled_non_bool_rejected`
- `test_mcp_toolset_default_config_enabled_non_bool_rejected`
- `test_mcp_toolset_rules_helper_empty_when_no_toolset` — unit test of `_build_mcp_toolset_rules` returning `{}` when no mcp_toolset in `tools`
- `test_mcp_toolset_rules_helper_merges_configs_and_default` — unit test with representative spec

### Success Criteria

#### Automated:
- [ ] `make test` passes (new validation tests green, existing tests unchanged)
- [ ] `make lint` passes
- [ ] Unit tests for `_build_mcp_toolset_rules` cover: no toolset, server-level deny only, per-tool deny only, mixed

#### Manual:
- [ ] POST /agents with `mcp_toolset.mcp_server_name: "doesnotexist"` returns 422 with a clear error message
- [ ] PUT /agents/{id} with the same error returns 422

**Gate**: Pause for review before Phase 2.

---

## Phase 2: Codex per-tool + per-server MCP deny

### Overview

Extend `_build_mcp_codex` in `src/fairy/sprites_exec.py:170-210` to consume `rules: dict[str, McpToolsetRules]` and emit the corresponding `[mcp_servers.<name>]` keys: `enabled`, `enabled_tools`, `disabled_tools`.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — extend `_build_mcp_codex`

```python
def _build_mcp_codex(
    servers: list[McpServerSpec],
    tools: list[dict] | None = None,
    rules: dict[str, McpToolsetRules] | None = None,
) -> str:
    """Generate Codex config TOML (MCP servers + tool enforcement keys)."""
    tools = tools or []
    rules = rules or {}
    top_level = _codex_top_level_keys(tools)

    if not servers and not top_level:
        return ""

    lines = ["# Codex configuration (MCP + tool enforcement)", "mkdir -p ~/.codex"]
    lines.append("cat > ~/.codex/config.toml << 'MCP_EOF'")
    if top_level:
        lines.extend(top_level)
        if servers:
            lines.append("")

    for s in servers:
        lines.append(f"[mcp_servers.{s.name}]")
        server_rules = rules.get(s.name)

        # Whole-server toggle
        if server_rules and not server_rules.default_enabled and not server_rules.per_tool:
            # default_enabled=False with no per-tool overrides → disable whole server
            lines.append("enabled = false")
        elif server_rules:
            # Per-tool allow/deny
            allowed = [t for t, e in server_rules.per_tool.items() if e]
            denied = [t for t, e in server_rules.per_tool.items() if not e]
            if not server_rules.default_enabled and allowed:
                # default deny, selective allow
                lines.append("enabled_tools = [" + ", ".join(f'"{t}"' for t in allowed) + "]")
            if denied:
                lines.append("disabled_tools = [" + ", ".join(f'"{t}"' for t in denied) + "]")

        # Existing url/stdio/auth emission unchanged
        if s.type == "url":
            lines.append(f'url = "{s.url}"')
            for key, val in s.headers.items():
                if key.lower() == "authorization" and val.startswith("Bearer "):
                    token = val.removeprefix("Bearer ").strip()
                    if token.startswith("${") and token.endswith("}"):
                        lines.append(f'bearer_token_env_var = "{token[2:-1]}"')
            lines.append("required = true")
        elif s.type == "stdio":
            # unchanged
            ...
        lines.append("")
    lines.append("MCP_EOF")
    return "\n".join(lines)
```

Note: `default_enabled=False` with no per-tool overrides emits `enabled = false`. If there are per-tool overrides, emit `enabled_tools`/`disabled_tools` instead (matching codex's allowlist-then-denylist semantics).

#### 2. `tests/test_tools_mcp.py` — codex unit tests

Add to a new class `TestCodexMcpToolsetRules`:
- `test_codex_no_rules_emits_current_behavior` — baseline, no change
- `test_codex_default_disabled_no_configs_emits_enabled_false`
- `test_codex_default_enabled_with_denied_tool_emits_disabled_tools`
- `test_codex_default_disabled_with_allowed_tool_emits_enabled_tools`
- `test_codex_mixed_allow_deny` — both enabled_tools and disabled_tools emitted
- `test_codex_rules_do_not_affect_server_not_in_rules` — only referenced server gets config
- `test_codex_rules_preserve_bearer_token_env_var` — auth still works

### Success Criteria

#### Automated:
- [ ] `make test` passes
- [ ] New unit tests cover each `McpToolsetRules` → TOML path
- [ ] `make lint` passes

#### Manual:
- [ ] Create a codex agent with mcp_toolset denying a tool; inspect the wrapper script (via a fake sprite run or direct unit-test output) and confirm `disabled_tools` appears

**Gate**: Pause for review before Phase 3.

---

## Phase 3: Gemini MCP deny via `excludeTools` in settings.json

### Overview

Extend `_build_mcp_gemini` in `src/fairy/sprites_exec.py:213-233` to emit `excludeTools` arrays on each `mcpServers` entry based on `rules`. We do NOT touch `_tool_files_gemini` (built-in tool Policy Engine) — MCP gets its own mechanism in the same settings.json file that `_build_mcp_gemini` already writes.

### Why `excludeTools` over Policy Engine

Gemini Policy Engine uses `mcp_{server}_{tool}` single-underscore rule names. A user-picked server alias containing an underscore (`my_github`) will misparse. `excludeTools` lives on the `mcpServers` entry and takes tool names directly (no naming transform). Co-locating MCP config with MCP restrictions also reduces file-writer sprawl.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — extend `_build_mcp_gemini`

```python
def _build_mcp_gemini(
    servers: list[McpServerSpec],
    rules: dict[str, McpToolsetRules] | None = None,
) -> str:
    rules = rules or {}
    config: dict[str, dict] = {}
    for s in servers:
        if s.type == "url":
            entry: dict = {"httpUrl": s.url, "trust": True}
            if s.headers:
                entry["headers"] = s.headers
        elif s.type == "stdio":
            entry = {"command": s.command, "args": s.args, "trust": True}
            if s.env:
                entry["env"] = s.env

        server_rules = rules.get(s.name)
        if server_rules:
            if not server_rules.default_enabled and not server_rules.per_tool:
                # whole server blocked: include an empty includeTools to deny all
                entry["includeTools"] = []
            else:
                denied = [t for t, e in server_rules.per_tool.items() if not e]
                allowed = [t for t, e in server_rules.per_tool.items() if e]
                if not server_rules.default_enabled and allowed:
                    entry["includeTools"] = allowed
                if denied:
                    entry["excludeTools"] = denied

        config[s.name] = entry

    content = json.dumps({"mcpServers": config}, indent=2)
    return (
        "# MCP server configuration\n"
        "cat > ~/.gemini/settings.json << 'MCP_EOF'\n"
        f"{content}\n"
        "MCP_EOF\n"
    )
```

Whole-server deny uses `includeTools: []` (empty allowlist). Per-tool deny uses `excludeTools`. Default-deny with allowlist uses `includeTools`.

#### 2. `tests/test_tools_mcp.py` — gemini unit tests

Add to a new class `TestGeminiMcpToolsetRules`:
- `test_gemini_no_rules_emits_current_behavior`
- `test_gemini_default_disabled_no_configs_emits_empty_include_tools`
- `test_gemini_default_enabled_with_denied_tool_emits_exclude_tools`
- `test_gemini_default_disabled_with_allowed_tool_emits_include_tools`
- `test_gemini_rules_coexist_with_trust_and_headers`
- `test_gemini_rules_for_nonexistent_server_ignored` — defensive: rule keyed on a server not in servers[] should not leak into config

### Success Criteria

#### Automated:
- [ ] `make test` passes
- [ ] New unit tests cover each `McpToolsetRules` → JSON path
- [ ] `make lint` passes

#### Manual:
- [ ] Emitted `settings.json` validates as valid JSON
- [ ] Inspect emitted JSON for a deny case and confirm `excludeTools` or `includeTools: []` present

**Gate**: Pause for review before Phase 4.

---

## Phase 4: Claude MCP deny via `settings.json` `permissions.deny`

### Overview

Add a new writer `_tool_files_claude_mcp` that emits `~/.claude/settings.json` with `permissions.deny` rules for MCP tools. This is a new file pattern for claude (the existing tool-enforcement path uses CLI flags only). Invoked via `_build_tool_flags` when the runtime is `claude`/`claude-oauth` and `rules` contains non-empty entries.

Built-in tool deny via `--disallowedTools` stays unchanged — this writer is scoped to MCP rules only.

### Why a new writer

`--disallowedTools "mcp__server__tool"` silently fails in `-p` mode (Claude Code #12863). `--allowedTools "mcp__server__*"` wildcard also fails (#13077). The only working deny path is `settings.json` with `permissions.deny` entries shaped as `{"tool": "mcp__<server>__<tool>"}` or `{"tool": "mcp__<server>"}`.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — new `_tool_files_claude_mcp`

```python
CLAUDE_SETTINGS_PATH = "/home/sprite/.claude/settings.json"


def _tool_files_claude_mcp(
    rules: dict[str, McpToolsetRules],
) -> dict[str, str]:
    """Write ~/.claude/settings.json with permissions.deny rules for MCP tools."""
    if not rules:
        return {}

    deny: list[dict[str, str]] = []
    allow: list[dict[str, str]] = []
    for server_name, r in rules.items():
        if not r.default_enabled and not r.per_tool:
            # whole server blocked
            deny.append({"tool": f"mcp__{server_name}"})
            continue
        if not r.default_enabled:
            # default deny, selective allow
            deny.append({"tool": f"mcp__{server_name}"})
            for tool_name, enabled in r.per_tool.items():
                if enabled:
                    allow.append({"tool": f"mcp__{server_name}__{tool_name}"})
        else:
            # default allow, selective deny
            for tool_name, enabled in r.per_tool.items():
                if not enabled:
                    deny.append({"tool": f"mcp__{server_name}__{tool_name}"})

    if not deny and not allow:
        return {}

    settings: dict = {"permissions": {}}
    if allow:
        settings["permissions"]["allow"] = allow
    if deny:
        settings["permissions"]["deny"] = deny

    return {CLAUDE_SETTINGS_PATH: json.dumps(settings, indent=2) + "\n"}
```

#### 2. `src/fairy/sprites_exec.py` — thread rules through `_build_tool_flags`

```python
def _build_tool_flags(
    runtime_name: str,
    tools: list[dict],
    mcp_server_names: list[str],
    mcp_rules: dict[str, McpToolsetRules],
) -> tuple[str, dict[str, str]]:
    if not tools and not mcp_rules:
        return "", {}
    if runtime_name in ("claude", "claude-oauth"):
        flags = _tool_flags_claude(tools, mcp_server_names)
        files = _tool_files_claude_mcp(mcp_rules)
        return flags, files
    if runtime_name == "gemini":
        # gemini MCP deny is handled inside _build_mcp_gemini
        return "", _tool_files_gemini(tools)
    return "", {}
```

Note: deny rules for `mcp__<server>` (whole-server) must take priority over `--tools "mcp__<server>"` allowlist from `_tool_flags_claude`. Claude's documented precedence is `deny > allow`, so the combination is safe.

#### 3. `tests/test_tools_mcp.py` — claude unit tests

Add to a new class `TestClaudeMcpToolsetRules`:
- `test_claude_no_rules_emits_no_settings_file`
- `test_claude_whole_server_deny_emits_mcp_server_in_deny_list`
- `test_claude_per_tool_deny_emits_full_tool_name_in_deny_list`
- `test_claude_default_disabled_with_allow_emits_both_allow_and_deny`
- `test_claude_settings_json_is_valid_json`
- `test_claude_mcp_rules_coexist_with_disallowed_tools_cli_flag` — both paths active, no conflict

### Success Criteria

#### Automated:
- [ ] `make test` passes
- [ ] Unit tests cover each `McpToolsetRules` → settings.json path
- [ ] `make lint` passes

#### Manual:
- [ ] Run a manual claude session with a test MCP server and a `permissions.deny` rule; confirm the denied tool is absent from the init event's visible toolset

**Gate**: Pause for review before Phase 5.

---

## Phase 5: Fairy-hosted test MCP server endpoint

### Overview

Implement a Django view at `/test-mcp` that serves the MCP Streamable HTTP protocol via the `mcp` Python SDK's `FastMCP` class. Gated behind `settings.DEBUG or settings.TESTING`. Three tools: `signal_tool`, `echo`, `dangerous_tool`. Optional bearer-token check via `MCP_TEST_TOKEN` env var.

### Changes Required

#### 1. `pyproject.toml` — add `mcp` dependency

Add `mcp>=1.0` to `[project.optional-dependencies].e2e`. Also add to `[project.optional-dependencies].dev` so developers can run the endpoint locally.

#### 2. `src/fairy/test_mcp.py` — FastMCP Django view

```python
"""MCP test server mounted at /test-mcp when DEBUG or TESTING.

Deterministic tools for e2e tests. DO NOT enable in production.
"""

import os
from mcp.server.fastmcp import FastMCP
from django.http import HttpResponseForbidden
from django.conf import settings

mcp = FastMCP("fairy-test-mcp")


@mcp.tool()
def signal_tool(token: str) -> str:
    """Echoes a signal string for allow-list assertions."""
    return f"MCP_SIGNAL_{token}"


@mcp.tool()
def echo(msg: str) -> str:
    """Returns the input message verbatim."""
    return msg


@mcp.tool()
def dangerous_tool() -> str:
    """Returns a sentinel string for deny-list assertions. Test tool only."""
    return "SHOULD_NOT_BE_CALLED"


def mcp_streamable_http_view(request):
    if not (settings.DEBUG or getattr(settings, "TESTING", False)):
        return HttpResponseForbidden("test-mcp disabled")

    expected = os.environ.get("MCP_TEST_TOKEN")
    if expected is not None:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ") != expected:
            return HttpResponseForbidden("bad MCP token")

    return mcp.handle_streamable_http(request)  # exact binding TBD — see note below
```

Note: the exact Django ↔ `mcp` Python SDK binding depends on whether `FastMCP` exposes a direct ASGI/WSGI handler or requires a shim. If needed, a small WSGI wrapper lives in the same file.

#### 3. `src/config/urls.py` — mount behind DEBUG/TESTING

```python
if settings.DEBUG or getattr(settings, "TESTING", False):
    from fairy.test_mcp import mcp_streamable_http_view
    urlpatterns += [path("test-mcp", mcp_streamable_http_view)]
```

#### 4. `src/config/settings.py` — add `TESTING` flag

```python
TESTING = os.environ.get("FAIRY_TESTING", "").lower() in ("1", "true")
```

Set `FAIRY_TESTING=1` in the e2e Makefile target so the test endpoint is live for the test run on the Fairy server.

### Success Criteria

#### Automated:
- [ ] `curl -i http://localhost:8777/test-mcp -X POST -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"test","version":"1"},"capabilities":{}}}'` returns a valid MCP initialize response when `FAIRY_TESTING=1`
- [ ] Same curl returns 403 when `FAIRY_TESTING` is unset and `DEBUG=False`
- [ ] `tools/list` returns `signal_tool`, `echo`, `dangerous_tool`
- [ ] `tools/call` with `signal_tool(token="abc")` returns `"MCP_SIGNAL_abc"`
- [ ] `tools/call` with valid `Authorization: Bearer <token>` succeeds when `MCP_TEST_TOKEN` is set
- [ ] Same call with wrong token returns 403

#### Manual:
- [ ] In `make dev` mode (DEBUG=True), visit `/test-mcp` and confirm the endpoint responds

**Gate**: Pause for review before Phase 6. Phase 5 can overlap with Phase 4 if you spin up a second workstream.

---

## Phase 6: E2E matrix

### Overview

Create `tests/e2e/test_mcp_enforcement.py` with 5 test classes × up-to-3 runtimes = ~14 sessions. Register `mcp_matrix` marker, add `test-e2e-mcp` Makefile target, update README with the MCP enforcement capability table. Depends on Phases 1–5.

### Changes Required

#### 1. `tests/e2e/conftest.py` — `mcp_test_url` fixture

```python
@pytest.fixture(scope="session")
def mcp_test_url(fairy_url):
    url = os.environ.get("MCP_TEST_URL")
    if url:
        return url
    # Default: assume the Fairy under test has /test-mcp mounted
    return f"{fairy_url.rstrip('/')}/test-mcp"
```

#### 2. `tests/e2e/test_mcp_enforcement.py` — full matrix

File skeleton follows the structure in `thoughts/research/2026-04-17-mcp-enforcement-e2e.md` Track 4 (see research doc for the complete code). Key elements:

- `pytestmark = [pytest.mark.slow, pytest.mark.mcp_matrix]`
- Constants: `MCP_TEST_SERVER_NAME = "testmcp"` (no underscore — avoids gemini Policy Engine parse issue that `_build_mcp_gemini` doesn't hit but belt-and-suspenders), `MCP_TEST_TOOL_NAME = "signal_tool"`, `MCP_TEST_TOOL_RESPONSE_PREFIX = "MCP_SIGNAL_"`.
- Parsers: three `_parse_<runtime>_mcp_tool_names` functions that reuse the existing `test_agent_tools.py` parse patterns and filter on `mcp__` prefix.
- Helpers: `_mcp_server_spec(url)`, `_mcp_toolset_allow_server()`, `_mcp_toolset_deny_server()`, `_mcp_toolset_deny_one_tool()`.
- Fixture: class-scoped `runtime = claude | codex | gemini` matching `E2E_RUNTIMES`.

**Test classes:**

| Class | Scenario | Runtimes | Sessions |
|---|---|---|---|
| `TestMcpServerToolInvocable` | server declared, no restriction → tool invoked + signal in output | claude, codex, gemini | 3 |
| `TestMcpDenySpecificTool` | `configs` denies `signal_tool` → not invoked | claude, codex, gemini | 3 |
| `TestMcpDenyEntireServer` | `default_config.enabled=False` → no mcp tool at all | claude, codex, gemini | 3 |
| `TestNoMcpServerNoMcpTool` | no `mcp_servers` → no `mcp__*` calls | claude, codex, gemini | 3 |
| `TestMcpDenyPersistsAcrossTurns` | deny persists on POST /prompt | claude only | 2 |

Total: ~14 sessions.

**Assertion shapes:**

- Allow: `_mcp_tool_was_invoked(events, runtime)` is True AND `MCP_SIGNAL_` substring in `stream_all_output(events)`.
- Deny: `not _any_mcp_tool_was_invoked(events, runtime)` AND `"SHOULD_NOT_BE_CALLED"` NOT in output.
- Absence: `not _any_mcp_tool_was_invoked(events, runtime)`.

#### 3. `pyproject.toml` — register marker

```toml
[tool.pytest.ini_options]
markers = [
    "slow: tests that create real agent sessions (may take minutes)",
    "tool_matrix: verifies agent tool enforcement across runtimes",
    "mcp_matrix: verifies MCP server + mcp_toolset enforcement across runtimes",
]
```

#### 4. `Makefile` — `test-e2e-mcp` target

```makefile
test-e2e-mcp:
	uv run pytest tests/e2e/test_mcp_enforcement.py -v -m "mcp_matrix"
```

Follow the pattern in `test-e2e-tools`: default `FAIRY_API_URL=http://localhost:8777`, require `FAIRY_API_TOKEN`, respect `E2E_RUNTIMES`.

#### 5. `README.md` — MCP enforcement capability table

Add a new subsection to the existing `## Tools` / `### Enforcement per runtime` area:

```markdown
### MCP tool enforcement per runtime

| Scenario | claude / claude-oauth | codex | gemini |
| --- | --- | --- | --- |
| Server declared, no restriction | ✓ | ✓ | ✓ |
| `mcp_toolset default_config.enabled=false` (deny server) | ✓ via settings.json | ✓ via `enabled = false` | ✓ via `includeTools=[]` |
| `mcp_toolset configs[].enabled=false` (deny specific tool) | ✓ via settings.json | ✓ via `disabled_tools` | ✓ via `excludeTools` |
| `default_config.enabled=false` + allow specific tool | ✓ via settings.json | ✓ via `enabled_tools` | ✓ via `includeTools` |

All MCP enforcement reapplies on multi-turn prompts (`POST /sessions/{id}/prompt`) because the wrapper script is rebuilt every call.
```

### Success Criteria

#### Automated:
- [ ] `make test-e2e-mcp E2E_RUNTIMES=claude` passes — 4 sessions green
- [ ] `make test-e2e-mcp E2E_RUNTIMES=claude,codex,gemini` passes — ~14 sessions green
- [ ] `make test-e2e-mcp` full-matrix total cost under $0.70 (check via cost logging in session result)
- [ ] `make test-e2e-tools` still passes unchanged
- [ ] `make lint` passes

#### Manual:
- [ ] Run `E2E_RUNTIMES=claude make test-e2e-mcp` against a local Fairy and visually confirm the test output shows the expected allow/deny outcomes per test
- [ ] Review README capability table renders correctly in GitHub

**Gate**: Ready to merge.

---

## Testing Strategy

### Automated

- **Unit tests** (Phases 1–4, `tests/test_tools_mcp.py`): exhaustively cover `_build_mcp_toolset_rules` and each runtime's emission path. No dollar cost. Every `McpToolsetRules` configuration (default-allow, default-deny, mixed, empty) has a test per runtime.
- **API validation tests** (Phase 1, `tests/test_tools_mcp.py`): cover the tightened `_validate_tools` for `mcp_toolset` — 422 cases for unknown server name, bad types, malformed configs.
- **Test server tests** (Phase 5): direct curl / unit tests against the FastMCP view, no agent sessions.
- **E2E matrix** (Phase 6): ~14 real agent sessions. Gated behind `FAIRY_API_TOKEN`, `MCP_TEST_URL` (defaults to `$FAIRY_API_URL/test-mcp`), and `mcp_matrix` marker.

### Manual

1. Create an agent with `mcp_servers: [{name: "testmcp", url: "$FAIRY_URL/test-mcp"}]` and `tools: [{type: "mcp_toolset", mcp_server_name: "testmcp", default_config: {enabled: false}}]`.
2. Create a session. Stream the output. Prompt the agent to "use the signal_tool from the testmcp server."
3. Confirm: the runtime init event shows the server registered; the stream contains no `mcp__testmcp__signal_tool` invocation; the session completes successfully with the model declining or unable to use the tool.
4. Repeat across claude, codex, gemini.
5. POST a second prompt to the session. Confirm the restriction still holds.

## Performance Considerations

- Each phase's unit tests run in the existing pytest suite — no added runtime cost (translation helpers are pure functions).
- E2E matrix adds ~14 sessions per full `test-e2e-mcp` run. Cost: ~$0.30–0.70 on haiku/o4-mini/gemini-flash. This is about 2× the `test-e2e-tools` cost because there are 14 sessions instead of 11.
- Test MCP server endpoint is stateless and ~10ms per tool call. No concern for the test load.
- `FAIRY_TESTING=1` in `make dev` would expose `/test-mcp` locally — safe for developers. Production deployments must not set it.

## References

- Research: [`thoughts/research/2026-04-17-mcp-enforcement-e2e.md`](../research/2026-04-17-mcp-enforcement-e2e.md)
- Sibling plan (tool enforcement, shipped as `4c3b9cc`): [`thoughts/plans/2026-04-16-agent-tool-enforcement.md`](./2026-04-16-agent-tool-enforcement.md)
- Built-in tool matrix test (pattern to mirror): `tests/e2e/test_agent_tools.py:1-217`
- Existing MCP emission: `src/fairy/sprites_exec.py:120-233`
- `_tool_files_gemini` Policy Engine writer (pattern for new settings.json writer): `src/fairy/sprites_exec.py:342-392`
- Codex `mcp_tool_call` stream parsing: `tests/e2e/test_agent_tools.py:89-92`
- Claude upstream MCP denial bugs: [#12863](https://github.com/anthropics/claude-code/issues/12863), [#13077](https://github.com/anthropics/claude-code/issues/13077)
