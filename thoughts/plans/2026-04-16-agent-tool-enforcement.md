# Agent Tool Enforcement Implementation Plan

## Overview

Wire Fairy's `Agent.tools` field through to the coding-agent runtime CLIs so that tools declared as disabled in the Managed-Agents spec are actually not invocable by the running agent. Add a focused e2e test suite that proves the translation works end-to-end.

Today, `Agent.tools` is stored, validated, and returned in API responses but never passed to `claude` / `codex` / `gemini` invocations — `sprites_exec.py` only wires `mcp_servers`. This plan closes that gap per-runtime and adds a tight e2e matrix to keep it closed.

## Research Summary

Research conducted by agent team with 4 specialist tracks — full document at `thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md`.

- **Claude CLI**: clean flags — `--tools "Bash,Read"` (allowlist) / `--disallowedTools "WebFetch"` (denylist), PascalCase names, no structured denial event. `--continue` does not persist restrictions.
- **Codex CLI**: coarse-grained only — `sandbox_mode` + per-MCP `enabled_tools`. Six of eight canonical tools have no per-tool enforcement; `web_search` is the only one with a dedicated on/off.
- **Gemini CLI**: no usable CLI flag — must write `~/.gemini/policies/*.toml` Policy Engine rules. `write` maps to two tools (`write_file` + `replace`). `--resume` re-reads files on every invocation.
- **Synthesis**: `Agent.tools` touches `sprites_exec.py:259` (`build_wrapper_script`), `views.py:225` (`create_session`), and the `send_prompt` continue path. Existing `_build_mcp_*` heredoc pattern is the template for the new `_tool_files_*` writers.

### Key Discoveries

- Tool-denial events are **universally absent** as structured stream types — assertion must check tool *absence*, never a denial event (`thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md`).
- Claude name casing translation is non-trivial: `bash → Bash`, `web_fetch → WebFetch`, etc. (`src/fairy/runtimes.py:5`).
- Codex has no `web_fetch` equivalent at all and no per-tool disable for bash/read/write/edit/glob/grep. Six hard gaps. (`thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md`, Track 2).
- Gemini Policy Engine workspace-level policies are broken (upstream issue #18186) — must write to `~/.gemini/policies/` user-level.
- Existing `_build_mcp_claude` / `_build_mcp_codex` / `_build_mcp_gemini` functions in `src/fairy/sprites_exec.py:120-206` are the pattern to follow for the new `_tool_files_*` functions.
- `Agent.tools` is already validated in `src/fairy/views.py:468`; `VALID_TOOL_TYPES = {"agent_toolset_20260401", "mcp_toolset", "custom"}`. We'll remove `"custom"` as part of Phase 1.
- Existing e2e suite at `tests/e2e/test_sessions.py:21-47` has the class-scoped `runtime` fixture pattern to reuse.
- `RUNTIME_MODELS` in `tests/e2e/conftest.py:20-27` pins cheapest models — `claude-haiku-4-5`, `o4-mini`, `gemini-2.5-flash`, `claude-haiku-4-5` (for oauth).

## Current State Analysis

`Agent.tools` is stored, returned to clients, and carried through versioning — but not through execution. Specifically:

- `src/fairy/models.py:222` defines `tools = models.JSONField(default=list, blank=True)`
- `src/fairy/views.py:461` includes `tools` in `AGENT_VERSIONED_FIELDS`
- `src/fairy/views.py:468-485` validates tool-list shape (`_validate_tools`)
- `src/fairy/views.py:225` calls `build_wrapper_script(...)` with `mcp_servers=` but NOT `tools=`
- `src/fairy/sprites_exec.py:259-293` (`build_wrapper_script`) has no `tools` parameter

A user can create an agent with `tools: [{type: "agent_toolset_20260401", default_config: {enabled: false}}]` and Fairy will happily give them a session where every built-in tool is available. That's the gap.

## Desired End State

- Creating a session from an agent with `tools` configured produces a wrapper script that actually constrains the runtime.
- For claude/claude-oauth: restrictions translate to `--tools` / `--disallowedTools` flags on both initial and continue invocations.
- For gemini: restrictions translate to a `~/.gemini/policies/fairy.toml` file written alongside the existing settings.json.
- For codex: restrictions translate to `sandbox_mode` / `web_search` / per-MCP keys in `~/.codex/config.toml`. Per-tool gaps for bash/read/write/edit/glob/grep/web_fetch are accepted silently and documented.
- The `custom` tool type is removed from `VALID_TOOL_TYPES`. New creates with `{type: "custom"}` return 422.
- E2E test `tests/e2e/test_agent_tools.py` runs ~11 sessions per full run and proves each runtime actually enforces (and denies) one representative tool plus deny-all.
- Unit tests in `tests/test_sprites_exec.py` exhaustively cover the snake→PascalCase mapping, TOML generation, and codex config generation — at no per-test dollar cost.
- `README.md` includes a per-runtime tool-enforcement capability table so users can see which tools are actually enforceable on each runtime.

### Verification
- `make test` passes (new unit tests green).
- `make test-e2e-tools E2E_RUNTIMES=claude` passes against a running Fairy — all 4 claude-path tests green.
- `make test-e2e-tools` with all runtimes runs ~11 sessions, completes under $0.15, all asserted behaviors enforced.
- `make lint` + `make test-e2e-fast` continue to pass unchanged.

## What We're NOT Doing

- **Custom tools**: removing `{type: "custom"}` from the validator. Not adding any schema injection or per-runtime custom-tool translation.
- **Validator 422s for unenforceable configs**: a codex agent with `web_fetch: enabled` in its tools spec is accepted silently. Documented capability gap, not an error.
- **Exhaustive per-tool e2e matrix**: the 4 runtimes × 8 tools × 2 polarity = 64-session matrix is explicitly out. Unit tests exhaust the translation surface; e2e tests prove the integration claim with one representative tool per runtime.
- **MCP tool enforcement e2e**: `TestMcpToolset` remains `@skip` pending a live MCP test server (separate future work).
- **Partial enforcement for gemini's `read_many_files` / `list_directory`**: the `read` canonical name maps only to `read_file`. Users who need the other read-adjacent tools restricted need to use MCP-level or shell-level policies separately.

## Implementation Approach

Five phases, strictly sequential through Phase 4 (they all touch `sprites_exec.py`), then Phases 5 and 6 can overlap. Unit tests land alongside each runtime phase to keep the translation-layer changes grounded. E2E tests land only once all runtime paths are wired — they're the integration check, not the per-unit check.

## File Ownership Map

| Phase | Files | Owner Track | Change Type |
|-------|-------|-------------|-------------|
| 1 | `src/fairy/sprites_exec.py` | backend | modify — add `_build_tool_flags()` skeleton |
| 1 | `src/fairy/views.py` | backend | modify — pass `tools` into `build_wrapper_script`; remove `"custom"` |
| 1 | `tests/test_sprites_exec.py` | backend | create — scaffolding tests |
| 2 | `src/fairy/sprites_exec.py` | backend | modify — claude implementation |
| 2 | `tests/test_sprites_exec.py` | backend | modify — claude unit tests |
| 3 | `src/fairy/sprites_exec.py` | backend | modify — gemini implementation |
| 3 | `tests/test_sprites_exec.py` | backend | modify — gemini unit tests |
| 4 | `src/fairy/sprites_exec.py` | backend | modify — codex implementation |
| 4 | `tests/test_sprites_exec.py` | backend | modify — codex unit tests |
| 5 | `tests/e2e/test_agent_tools.py` | tests | create |
| 6 | `pyproject.toml` | meta | modify — register `tool_matrix` marker |
| 6 | `Makefile` | meta | modify — add `test-e2e-tools` target |
| 6 | `README.md` | meta | modify — capability table |

**Conflict-free guarantee**: Phases 2–4 each modify `sprites_exec.py` and are strictly sequential. Phases 5 and 6 touch disjoint files and may run in parallel after Phase 4 merges.

---

## Phase 1: Wiring foundation + custom-tools descope

### Overview

Introduce `_build_tool_flags(runtime, tools) -> (cli_flags, files)` as a pure dispatcher that returns empty for every runtime. Thread it through `build_wrapper_script` and the two call sites in `views.py`. Also remove `"custom"` from `VALID_TOOL_TYPES`. At the end of Phase 1, `agent.tools` flows end-to-end but has no behavioral effect — only plumbing. This keeps the diff small and lets every subsequent runtime phase land independently.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — add dispatcher skeleton

Add the new function with a per-runtime switch that returns empty for all runtimes:

```python
def _build_tool_flags(
    runtime_name: str,
    tools: list[dict],
    mcp_server_names: list[str],
) -> tuple[str, dict[str, str]]:
    """
    Translate Agent.tools into runtime-specific enforcement.

    Returns (cli_flags, files):
      cli_flags: extra flags appended to the exec line (claude only, for now)
      files: {absolute_path: content} heredoc'd into the wrapper script
    """
    if not tools:
        return "", {}
    if runtime_name in ("claude", "claude-oauth"):
        return _tool_flags_claude(tools, mcp_server_names), {}
    if runtime_name == "codex":
        return "", _tool_files_codex(tools)
    if runtime_name == "gemini":
        return "", _tool_files_gemini(tools)
    return "", {}


def _tool_flags_claude(tools: list[dict], mcp_server_names: list[str]) -> str:
    """Phase 2 — returns empty for now."""
    return ""


def _tool_files_codex(tools: list[dict]) -> dict[str, str]:
    """Phase 4 — returns empty for now."""
    return {}


def _tool_files_gemini(tools: list[dict]) -> dict[str, str]:
    """Phase 3 — returns empty for now."""
    return {}
```

Extend `build_wrapper_script` to accept `tools` and emit the returned files + flags:

```python
def build_wrapper_script(
    config: RuntimeConfig,
    api_key: str,
    prompt: str,
    *,
    continue_session: bool = False,
    repos: list[RepoSpec] | None = None,
    environment: EnvironmentSetup | None = None,
    mcp_servers: list[McpServerSpec] | None = None,
    tools: list[dict] | None = None,
) -> str:
    cmd = config.continue_cmd if continue_session else config.cmd
    mcp_section = _build_mcp_section(config.name, mcp_servers or [])
    mcp_flags = _mcp_cmd_flags(config.name, mcp_servers or [])

    mcp_names = [s.name for s in (mcp_servers or [])]
    tool_flags, tool_files = _build_tool_flags(config.name, tools or [], mcp_names)
    tool_files_section = _format_tool_files_heredoc(tool_files)

    env_script = build_environment_script(environment=environment, repos=repos)
    header = "#!/bin/bash\nset -euo pipefail\n"
    body = env_script[len(header):]
    agent_exports = (
        f"export {config.env_var}={shlex.quote(api_key)}\n"
        f"export PROMPT={shlex.quote(prompt)}\n"
    )
    return f"""{header}{agent_exports}{body}
{mcp_section}
{tool_files_section}

exec {cmd}{mcp_flags}{tool_flags}
"""
```

Add `_format_tool_files_heredoc(files)` helper that renders each `path: content` as a `mkdir -p $(dirname); cat > $path << 'EOF' ... EOF` block.

#### 2. `src/fairy/views.py` — remove `"custom"`, pass `tools` through

At `views.py:464`:

```python
VALID_TOOL_TYPES = {"agent_toolset_20260401", "mcp_toolset"}
```

Delete the `custom`-specific validation branch at `views.py:479-482`.

At the two `build_wrapper_script(...)` call sites (line ~225 in `create_session`, and the equivalent in `send_prompt`), add `tools=agent_obj.tools`:

```python
script = build_wrapper_script(
    config, api_key, effective_prompt,
    repos=repo_specs,
    environment=env_setup,
    mcp_servers=mcp_specs,
    tools=agent_obj.tools,
)
```

For the `continue_session=True` path in `send_prompt`, ensure the same `tools=` kwarg is passed — restrictions do not persist on `--continue` in claude and need to be re-emitted.

#### 3. `tests/test_sprites_exec.py` — create file, add scaffolding tests

```python
import pytest
from fairy.sprites_exec import (
    build_wrapper_script,
    _build_tool_flags,
)
from fairy.runtimes import RUNTIMES


def test_build_tool_flags_empty_tools_returns_empty():
    flags, files = _build_tool_flags("claude", [], [])
    assert flags == ""
    assert files == {}


@pytest.mark.parametrize("runtime", ["claude", "claude-oauth", "codex", "gemini"])
def test_wrapper_script_accepts_tools_kwarg(runtime):
    """Smoke: passing tools= does not break wrapper generation."""
    config = RUNTIMES[runtime]
    script = build_wrapper_script(config, "fake-key", "hello", tools=[])
    assert script.startswith("#!/bin/bash")


def test_wrapper_script_tools_default_is_none():
    """Omitting tools= still works."""
    config = RUNTIMES["claude"]
    script = build_wrapper_script(config, "fake-key", "hello")
    assert "exec claude" in script
```

#### 4. Update existing view-level tests

`tests/test_agents.py` may have tests that create agents with `{type: "custom"}` — update those to use `agent_toolset_20260401` or delete the custom-specific tests. Grep first: `rg 'type.*custom' tests/`.

### Success Criteria

#### Automated Verification
- [ ] `make lint` passes
- [ ] `make test` passes (all existing + new scaffolding tests)
- [ ] `rg 'custom' src/fairy/views.py` returns no tool-type references
- [ ] `rg '"custom"' tests/` shows all custom-type test cases updated or removed

#### Manual Verification
- [ ] Creating an agent with `tools: [{"type": "custom", "name": "x"}]` returns 422 with message referring to allowed types
- [ ] Creating an agent with `tools: [{"type": "agent_toolset_20260401"}]` still succeeds and a session can be spawned (behavior unchanged — just plumbed through)

**Gate**: Pause before Phase 2. Confirm on a running Fairy that the existing e2e suite (`make test-e2e`) still passes.

---

## Phase 2: Claude runtime translation

### Overview

Implement `_tool_flags_claude(tools, mcp_server_names)`. Emits `--tools "Bash,Read"` for the default-off-allowlist case and `--disallowedTools "WebFetch,WebSearch"` for the default-on-denylist case. Handles the snake→PascalCase mapping and MCP tool references. Applies to both initial and `--continue` invocations automatically (wrapper script is rebuilt per call).

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — claude translator

Add a constant mapping table and the translator:

```python
_CLAUDE_TOOL_NAMES: dict[str, str] = {
    "bash": "Bash",
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "glob": "Glob",
    "grep": "Grep",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
}


def _tool_flags_claude(tools: list[dict], mcp_server_names: list[str]) -> str:
    """
    Translate Managed-Agents spec → claude CLI flags.

    default_config.enabled=False + no configs  → --tools ""
    default_config.enabled=False + some enabled → --tools "Bash,Read"
    default_config.enabled=True  + some disabled → --disallowedTools "WebFetch"
    default_config.enabled=True  + no configs   → no flag (all tools default)
    mcp_toolset entries add mcp__<server>__* to the relevant list.
    """
    toolset = _find_agent_toolset(tools)
    default_enabled = toolset.get("default_config", {}).get("enabled", True) if toolset else True
    configs = {c["name"]: c.get("enabled", True) for c in (toolset or {}).get("configs", [])}

    mcp_refs = [t["mcp_server_name"] for t in tools if t.get("type") == "mcp_toolset"]

    if not default_enabled:
        enabled_tools = [
            _CLAUDE_TOOL_NAMES[name]
            for name in _CLAUDE_TOOL_NAMES
            if configs.get(name, False)
        ]
        enabled_tools += [f"mcp__{name}" for name in mcp_refs]
        return f' --tools "{",".join(enabled_tools)}"'

    disabled_tools = [
        _CLAUDE_TOOL_NAMES[name]
        for name, enabled in configs.items()
        if name in _CLAUDE_TOOL_NAMES and not enabled
    ]
    if not disabled_tools:
        return ""
    return f' --disallowedTools "{",".join(disabled_tools)}"'


def _find_agent_toolset(tools: list[dict]) -> dict | None:
    for t in tools:
        if t.get("type") == "agent_toolset_20260401":
            return t
    return None
```

#### 2. `tests/test_sprites_exec.py` — exhaustive claude unit tests

```python
import pytest
from fairy.sprites_exec import _tool_flags_claude


class TestClaudeToolFlags:
    def test_empty_tools_returns_empty(self):
        assert _tool_flags_claude([], []) == ""

    def test_no_toolset_entry_returns_empty(self):
        # Only mcp_toolset, no agent_toolset → no flags needed
        assert _tool_flags_claude([{"type": "mcp_toolset", "mcp_server_name": "x"}], ["x"]) == ""

    def test_default_enabled_true_no_overrides_returns_empty(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": True}}]
        assert _tool_flags_claude(tools, []) == ""

    def test_default_enabled_false_no_overrides_disables_all(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        assert _tool_flags_claude(tools, []) == ' --tools ""'

    def test_default_enabled_false_with_allowlist(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [
                {"name": "bash", "enabled": True},
                {"name": "read", "enabled": True},
            ],
        }]
        flags = _tool_flags_claude(tools, [])
        # Order is deterministic: follows _CLAUDE_TOOL_NAMES insertion order
        assert flags == ' --tools "Bash,Read"'

    def test_default_enabled_true_with_denylist(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "web_fetch", "enabled": False},
                {"name": "web_search", "enabled": False},
            ],
        }]
        flags = _tool_flags_claude(tools, [])
        assert flags == ' --disallowedTools "WebFetch,WebSearch"'

    @pytest.mark.parametrize("canonical,pascal", [
        ("bash", "Bash"), ("read", "Read"), ("write", "Write"),
        ("edit", "Edit"), ("glob", "Glob"), ("grep", "Grep"),
        ("web_fetch", "WebFetch"), ("web_search", "WebSearch"),
    ])
    def test_every_canonical_name_maps_correctly(self, canonical, pascal):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": canonical, "enabled": False}],
        }]
        assert _tool_flags_claude(tools, []) == f' --disallowedTools "{pascal}"'

    def test_mcp_toolset_included_in_allowlist(self):
        tools = [
            {"type": "agent_toolset_20260401", "default_config": {"enabled": False}},
            {"type": "mcp_toolset", "mcp_server_name": "slack"},
        ]
        flags = _tool_flags_claude(tools, ["slack"])
        assert flags == ' --tools "mcp__slack"'
```

#### 3. Integration test in wrapper-script generation

```python
def test_wrapper_script_claude_includes_tool_flags():
    config = RUNTIMES["claude"]
    tools = [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True},
        "configs": [{"name": "web_fetch", "enabled": False}],
    }]
    script = build_wrapper_script(config, "key", "prompt", tools=tools)
    assert '--disallowedTools "WebFetch"' in script
```

### Success Criteria

#### Automated Verification
- [ ] `make test` passes, including all new `TestClaudeToolFlags` cases
- [ ] `rg '_CLAUDE_TOOL_NAMES' src/fairy/` shows exactly one definition
- [ ] `rg '\-\-tools ' src/fairy/` shows the flag is emitted from `_tool_flags_claude` only

#### Manual Verification
- [ ] On a running Fairy, create an agent with `tools: [{type: "agent_toolset_20260401", default_config: {enabled: false}}]`; spawn a session asking it to read a file; session completes without the model using `Read` (inspect the stream)

**Gate**: Human sanity-check that the Claude invocation actually gets the flag. `grep -o 'claude .*' run-agent.sh` inside a Sprite should show the emitted `--tools` / `--disallowedTools`.

---

## Phase 3: Gemini runtime translation

### Overview

Implement `_tool_files_gemini(tools)`. Returns a dict like `{"/root/.gemini/policies/fairy.toml": "<toml content>"}`. Handles the `write` → `write_file` + `replace` duplication and `read` → `read_file` mapping. The wrapper script's heredoc writer (added in Phase 1) will `mkdir -p ~/.gemini/policies` and write the file before `gemini` exec.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — gemini translator

```python
_GEMINI_TOOL_NAMES: dict[str, list[str]] = {
    "bash": ["run_shell_command"],
    "read": ["read_file"],
    "write": ["write_file", "replace"],  # write maps to TWO gemini tools
    "edit": ["replace"],
    "glob": ["glob"],
    "grep": ["grep_search"],
    "web_fetch": ["web_fetch"],
    "web_search": ["google_web_search"],
}

GEMINI_POLICY_PATH = "/home/sprite/.gemini/policies/fairy.toml"


def _tool_files_gemini(tools: list[dict]) -> dict[str, str]:
    toolset = _find_agent_toolset(tools)
    if toolset is None:
        return {}
    default_enabled = toolset.get("default_config", {}).get("enabled", True)
    configs = {c["name"]: c.get("enabled", True) for c in toolset.get("configs", [])}

    rules: list[str] = []

    if not default_enabled:
        # Deny-all, then allow explicitly enabled
        rules.append('[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 1\ninteractive = false')
        allowed_gemini_names: list[str] = []
        for canonical, enabled in configs.items():
            if enabled and canonical in _GEMINI_TOOL_NAMES:
                allowed_gemini_names.extend(_GEMINI_TOOL_NAMES[canonical])
        if allowed_gemini_names:
            names_toml = ", ".join(f'"{n}"' for n in allowed_gemini_names)
            rules.append(
                f'[[rule]]\ntoolName = [{names_toml}]\n'
                'decision = "allow"\npriority = 100\ninteractive = false'
            )
    else:
        # Deny only explicitly disabled
        for canonical, enabled in configs.items():
            if enabled or canonical not in _GEMINI_TOOL_NAMES:
                continue
            names_toml = ", ".join(f'"{n}"' for n in _GEMINI_TOOL_NAMES[canonical])
            rules.append(
                f'[[rule]]\ntoolName = [{names_toml}]\n'
                'decision = "deny"\ninteractive = false'
            )

    if not rules:
        return {}
    content = "\n\n".join(rules) + "\n"
    return {GEMINI_POLICY_PATH: content}
```

Update `_format_tool_files_heredoc` (added in Phase 1) to emit:

```bash
mkdir -p /home/sprite/.gemini/policies
cat > /home/sprite/.gemini/policies/fairy.toml << 'TOOLFILE_EOF'
<content>
TOOLFILE_EOF
```

#### 2. `tests/test_sprites_exec.py` — gemini unit tests

```python
class TestGeminiToolFiles:
    def test_no_toolset_returns_empty(self):
        from fairy.sprites_exec import _tool_files_gemini
        assert _tool_files_gemini([]) == {}

    def test_default_enabled_no_overrides_returns_empty(self):
        from fairy.sprites_exec import _tool_files_gemini
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": True}}]
        assert _tool_files_gemini(tools) == {}

    def test_write_disabled_denies_both_gemini_tools(self):
        """Managed-Agents 'write' must deny both write_file AND replace."""
        from fairy.sprites_exec import _tool_files_gemini, GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "write", "enabled": False}],
        }]
        files = _tool_files_gemini(tools)
        content = files[GEMINI_POLICY_PATH]
        assert '"write_file"' in content
        assert '"replace"' in content
        assert 'decision = "deny"' in content

    def test_default_enabled_false_emits_deny_all_rule(self):
        from fairy.sprites_exec import _tool_files_gemini, GEMINI_POLICY_PATH
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        content = _tool_files_gemini(tools)[GEMINI_POLICY_PATH]
        assert 'toolName = "*"' in content
        assert 'decision = "deny"' in content

    def test_default_off_with_allowlist(self):
        from fairy.sprites_exec import _tool_files_gemini, GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [{"name": "bash", "enabled": True}],
        }]
        content = _tool_files_gemini(tools)[GEMINI_POLICY_PATH]
        assert 'toolName = "*"' in content  # deny-all
        assert '"run_shell_command"' in content  # allow bash
        assert content.count("decision = \"deny\"") == 1
        assert content.count("decision = \"allow\"") == 1

    def test_interactive_false_set_on_every_rule(self):
        """Fairy runs headless — rules must be scoped to headless mode."""
        from fairy.sprites_exec import _tool_files_gemini, GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "bash", "enabled": False}],
        }]
        content = _tool_files_gemini(tools)[GEMINI_POLICY_PATH]
        assert "interactive = false" in content
```

#### 3. Integration test

```python
def test_wrapper_script_gemini_writes_policy_file():
    config = RUNTIMES["gemini"]
    tools = [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True},
        "configs": [{"name": "bash", "enabled": False}],
    }]
    script = build_wrapper_script(config, "key", "prompt", tools=tools)
    assert "/home/sprite/.gemini/policies/fairy.toml" in script
    assert 'toolName = ["run_shell_command"]' in script
    assert "TOOLFILE_EOF" in script  # heredoc wrapper
```

### Success Criteria

#### Automated Verification
- [ ] `make test` passes, including all new `TestGeminiToolFiles` cases
- [ ] `rg 'fairy.toml' src/` shows exactly one path definition
- [ ] Integration test proving the heredoc wraps correctly

#### Manual Verification
- [ ] On a running Fairy, create a gemini agent with `tools: [{type: "agent_toolset_20260401", default_config: {enabled: true}, configs: [{name: "bash", enabled: false}]}]`; spawn a session asking it to run a shell command; session completes and the bash call is either absent from stream or appears as a `tool_result` with `error.message` containing "denied by policy"

**Gate**: Confirm the TOML file actually lands on the Sprite — spawn a session, snapshot the wrapper script, verify the heredoc + mkdir emitted correctly.

---

## Phase 4: Codex runtime translation

### Overview

Implement `_tool_files_codex(tools)`. Extends the existing `~/.codex/config.toml` emission (currently only `[mcp_servers.*]` sections) with top-level `sandbox_mode` and `web_search` keys, plus per-MCP `enabled_tools` / `disabled_tools` if applicable. Six of the eight canonical tools cannot be enforced per-tool on codex — those are accepted silently and documented.

### Changes Required

#### 1. `src/fairy/sprites_exec.py` — codex translator

Because `_build_mcp_codex` already generates the config.toml, we need to either (a) merge tool-config output into that function or (b) return a separate config-toml patch that `_format_tool_files_heredoc` concatenates. Approach (a) is cleaner — update `_build_mcp_codex` to accept `tools` and emit top-level keys before the `[mcp_servers.*]` sections.

```python
def _build_mcp_codex(
    servers: list[McpServerSpec],
    tools: list[dict] | None = None,
) -> str:
    tools = tools or []
    toolset = _find_agent_toolset(tools)
    default_enabled = (toolset or {}).get("default_config", {}).get("enabled", True) if toolset else True
    configs = {c["name"]: c.get("enabled", True) for c in (toolset or {}).get("configs", [])}

    lines = ["# MCP + tools configuration", "mkdir -p ~/.codex"]
    lines.append("cat > ~/.codex/config.toml << 'CODEX_EOF'")

    # Top-level codex tool enforcement keys
    # web_search: the only per-tool enforceable canonical in codex
    if not default_enabled and not configs.get("web_search", False):
        lines.append('web_search = "disabled"')
    elif default_enabled and configs.get("web_search") is False:
        lines.append('web_search = "disabled"')

    # sandbox_mode: coarse write protection — only if BOTH write and edit are disabled
    # (edit on codex always collapses to shell, but we still respect the write signal)
    write_disabled = (not default_enabled and not configs.get("write", False)) \
        or (default_enabled and configs.get("write") is False)
    edit_disabled = (not default_enabled and not configs.get("edit", False)) \
        or (default_enabled and configs.get("edit") is False)
    if write_disabled and edit_disabled:
        lines.append('sandbox_mode = "read-only"')

    # Existing MCP emission — unchanged
    for s in servers:
        lines.append(f"[mcp_servers.{s.name}]")
        # ... (existing logic)
        lines.append("")
    lines.append("CODEX_EOF")
    return "\n".join(lines)
```

Update the dispatcher `_build_mcp_section` and `_build_tool_flags` so codex's `tools` flows through `_build_mcp_codex` (rather than `_tool_files_codex`) — keeps the single config.toml source. Delete the `_tool_files_codex` stub; codex work happens in `_build_mcp_codex`.

Update the single call site in `build_wrapper_script`:

```python
if config.name == "codex":
    mcp_section = _build_mcp_codex(mcp_servers or [], tools=tools)
else:
    mcp_section = _build_mcp_section(config.name, mcp_servers or [])
```

#### 2. `tests/test_sprites_exec.py` — codex unit tests

```python
class TestCodexToolConfig:
    def test_no_tools_no_mcp_returns_empty_config(self):
        from fairy.sprites_exec import _build_mcp_codex
        assert _build_mcp_codex([], tools=[]) == ""

    def test_web_search_disabled_writes_key(self):
        from fairy.sprites_exec import _build_mcp_codex
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "web_search", "enabled": False}],
        }]
        content = _build_mcp_codex([], tools=tools)
        assert 'web_search = "disabled"' in content

    def test_write_and_edit_disabled_sets_read_only_sandbox(self):
        from fairy.sprites_exec import _build_mcp_codex
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "write", "enabled": False},
                {"name": "edit", "enabled": False},
            ],
        }]
        content = _build_mcp_codex([], tools=tools)
        assert 'sandbox_mode = "read-only"' in content

    def test_only_write_disabled_does_not_set_sandbox(self):
        """Sandbox mode is blunt — only flip it if both write AND edit are off."""
        from fairy.sprites_exec import _build_mcp_codex
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "write", "enabled": False}],
        }]
        content = _build_mcp_codex([], tools=tools)
        assert "sandbox_mode" not in content

    def test_default_off_no_configs_disables_web_search(self):
        from fairy.sprites_exec import _build_mcp_codex
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        content = _build_mcp_codex([], tools=tools)
        assert 'web_search = "disabled"' in content

    def test_unenforceable_tools_are_silent(self):
        """bash/read/glob/grep/web_fetch disable produces no config — accepted silently."""
        from fairy.sprites_exec import _build_mcp_codex
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "bash", "enabled": False},
                {"name": "read", "enabled": False},
                {"name": "glob", "enabled": False},
                {"name": "grep", "enabled": False},
                {"name": "web_fetch", "enabled": False},
            ],
        }]
        content = _build_mcp_codex([], tools=tools)
        # No sandbox_mode (only write+edit triggers it), no web_search (it was default-on)
        assert "sandbox_mode" not in content
        assert "web_search" not in content
```

### Success Criteria

#### Automated Verification
- [ ] `make test` passes, including all `TestCodexToolConfig` cases
- [ ] Existing codex MCP tests still pass (the refactor of `_build_mcp_codex` shouldn't break them)

#### Manual Verification
- [ ] On a running Fairy, create a codex agent with `tools: [{type: "agent_toolset_20260401", default_config: {enabled: true}, configs: [{name: "web_search", enabled: false}]}]`; spawn a session asking it to search the web; session completes without invoking web_search (inspect JSONL stream — no `item.completed` with `type: "web_search"`)
- [ ] Same test with `write` + `edit` both disabled produces a session whose sandbox is read-only (model attempts to write, gets error)

**Gate**: Confirm Phases 2+3+4 all landed cleanly. Phase 5's test module will exercise them end-to-end.

---

## Phase 5: E2E test module

### Overview

Create `tests/e2e/test_agent_tools.py`. Small focused matrix: 3 canonical tests × 3 enforceable runtimes (claude, codex, gemini) + 1 claude-oauth smoke + 1 multi-turn persistence = **~11 real sessions per full run, ~$0.12 at cheapest models**.

Pick one representative tool per runtime — unit tests already cover the full name-mapping surface, so e2e only needs to prove integration works.

### Representative tool per runtime
- `claude` + `claude-oauth` → `web_fetch` (easy prompt, clean `WebFetch` signal, disable via `--disallowedTools "WebFetch"`)
- `codex` → `web_search` (the only per-tool enforceable codex canonical)
- `gemini` → `bash` / `run_shell_command` (Policy Engine deny path is best-documented)

### Changes Required

#### 1. `tests/e2e/test_agent_tools.py` — new file

```python
"""E2E tests verifying Agent.tools field is enforced by each runtime CLI.

Tight matrix — unit tests cover per-tool translation; these only prove the
translation layer integrates end-to-end. ~11 sessions per full run.
"""

from __future__ import annotations

import json
import pytest

from tests.e2e.conftest import (
    RUNTIME_MODELS,
    FairyClient,
    _unique,
    stream_all_output,
)

pytestmark = [pytest.mark.slow, pytest.mark.tool_matrix]

# Representative tool per runtime — the one the matrix tests use.
REPRESENTATIVE_TOOL = {
    "claude": "web_fetch",
    "claude-oauth": "web_fetch",
    "codex": "web_search",
    "gemini": "bash",
}

# Canonical tool name → runtime's CLI/stream name
RUNTIME_TOOL_NAMES = {
    "claude": {"web_fetch": "WebFetch"},
    "claude-oauth": {"web_fetch": "WebFetch"},
    "codex": {"web_search": "web_search"},
    "gemini": {"bash": "run_shell_command"},
}

PROMPTS = {
    "web_fetch": "Use your web fetch tool to fetch https://httpbin.org/get and print the response body. Do not use shell.",
    "web_search": "Use your web search tool to search for 'current UTC date' and print the top result's title.",
    "bash": "Run the shell command `echo TOOL_SIGNAL_BASH` using your shell tool and print the output.",
}


# ---------------------------------------------------------------------------
# Stream parsers
# ---------------------------------------------------------------------------


def _parse_claude_tool_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for e in events:
        if e.get("type") != "output":
            continue
        try:
            obj = json.loads(e.get("data", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if obj.get("type") == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name")
                    if name:
                        names.append(name)
    return names


def _parse_codex_tool_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for e in events:
        if e.get("type") != "output":
            continue
        try:
            obj = json.loads(e.get("data", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        # codex uses item-level types inside item.started / item.completed events
        if obj.get("type") in ("item.started", "item.completed"):
            item_type = obj.get("item", {}).get("type")
            if item_type == "web_search":
                names.append("web_search")
            elif item_type == "command_execution":
                names.append("shell")
            elif item_type == "mcp_tool_call":
                names.append(f"mcp__{obj['item'].get('server')}__{obj['item'].get('tool')}")
    return names


def _parse_gemini_tool_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for e in events:
        if e.get("type") != "output":
            continue
        try:
            obj = json.loads(e.get("data", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if obj.get("type") == "tool_use":
            name = obj.get("tool_name")
            if name:
                names.append(name)
    return names


def _tool_was_invoked(events: list[dict], runtime: str, tool: str) -> bool:
    target = RUNTIME_TOOL_NAMES[runtime][tool]
    if runtime in ("claude", "claude-oauth"):
        return target in _parse_claude_tool_names(events)
    if runtime == "codex":
        return target in _parse_codex_tool_names(events)
    if runtime == "gemini":
        return target in _parse_gemini_tool_names(events)
    return False


def _any_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    """Used by deny-all to check NOTHING ran."""
    if runtime in ("claude", "claude-oauth"):
        return bool(_parse_claude_tool_names(events))
    if runtime == "codex":
        # Shell-only tools that codex emits even on no-op; filter to the
        # canonical set we control
        return bool(_parse_codex_tool_names(events))
    if runtime == "gemini":
        return bool(_parse_gemini_tool_names(events))
    return False


# ---------------------------------------------------------------------------
# Toolset builders
# ---------------------------------------------------------------------------


def _allow_only(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": False},
        "configs": [{"name": tool, "enabled": True}],
    }]


def _deny(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True},
        "configs": [{"name": tool, "enabled": False}],
    }]


def _deny_all() -> list[dict]:
    return [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]


# ---------------------------------------------------------------------------
# Runtime fixture — claude-oauth tested separately (smoke only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class", params=["claude", "codex", "gemini"])
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


# ---------------------------------------------------------------------------
# Matrix tests — 3 runtimes × 3 tests = 9 sessions
# ---------------------------------------------------------------------------


class TestToolEnforcement:
    """Core matrix — prove each runtime's translation layer works end-to-end."""

    def test_allow_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        tool = REPRESENTATIVE_TOOL[runtime]
        agent = create_agent(
            name=_unique(f"e2e-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_allow_only(tool),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=PROMPTS[tool],
            timeout=120,
        )
        final, events = api.run_session(session["id"])
        assert final["status"] == "completed", (
            f"Allow test failed: status={final['status']}, "
            f"exit_code={final.get('exit_code')}\n"
            f"Output: {stream_all_output(events)[:500]}"
        )
        assert _tool_was_invoked(events, runtime, tool), (
            f"Expected '{tool}' ({RUNTIME_TOOL_NAMES[runtime][tool]}) to be invoked.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )

    def test_deny_tool_not_invoked(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        tool = REPRESENTATIVE_TOOL[runtime]
        agent = create_agent(
            name=_unique(f"e2e-deny-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_deny(tool),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=PROMPTS[tool],
            timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, runtime, tool), (
            f"Tool '{tool}' was invoked despite being disabled.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )

    def test_deny_all_blocks_everything(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        agent = create_agent(
            name=_unique(f"e2e-denyall-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_deny_all(),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Run `echo hello`, read /etc/hostname, and list /tmp files.",
            timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _any_tool_was_invoked(events, runtime), (
            f"Tool was invoked despite deny-all.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )


# ---------------------------------------------------------------------------
# Claude-oauth smoke — 1 session
# ---------------------------------------------------------------------------


class TestClaudeOAuthSmoke:
    """Prove the claude-oauth path receives the same tool flags as claude."""

    def test_oauth_agent_respects_deny(
        self, api: FairyClient, create_agent, create_session, e2e_runtimes,
    ):
        if "claude-oauth" not in e2e_runtimes:
            pytest.skip("claude-oauth not in E2E_RUNTIMES")
        agent = create_agent(
            name=_unique("e2e-oauth-smoke"),
            model=RUNTIME_MODELS["claude-oauth"],
            runtime="claude-oauth",
            tools=_deny("web_fetch"),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=PROMPTS["web_fetch"],
            timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, "claude-oauth", "web_fetch")


# ---------------------------------------------------------------------------
# Multi-turn persistence — 1 session (claude only)
# ---------------------------------------------------------------------------


class TestMultiTurnPersistence:
    """Restrictions must re-apply on POST /sessions/{id}/prompt.

    Claude --continue does NOT persist CLI flags; the wrapper script is rebuilt
    per call. This test proves Fairy re-emits the flag.
    """

    def test_deny_persists_across_turns(
        self, api: FairyClient, create_agent, create_session, e2e_runtimes,
    ):
        if "claude" not in e2e_runtimes:
            pytest.skip("claude not in E2E_RUNTIMES")
        agent = create_agent(
            name=_unique("e2e-multi-turn"),
            model=RUNTIME_MODELS["claude"],
            runtime="claude",
            tools=_deny("web_fetch"),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say hello.",
            timeout=60,
        )
        api.run_session(session["id"])

        # Second turn: ask it to fetch a URL
        resp = api.send_prompt(
            session["id"],
            prompt=PROMPTS["web_fetch"],
            timeout=120,
        )
        assert resp.status_code == 202
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, "claude", "web_fetch"), (
            "web_fetch was invoked on turn 2 — restrictions did not persist."
        )
```

### Success Criteria

#### Automated Verification
- [ ] `make test-e2e-tools E2E_RUNTIMES=claude` passes — 4 tests green (3 matrix + 1 multi-turn)
- [ ] `make test-e2e-tools E2E_RUNTIMES=claude,codex,gemini` passes — 9 tests green
- [ ] `make test-e2e-tools E2E_RUNTIMES=claude-oauth` passes the smoke
- [ ] `make test-e2e-fast` does NOT run `tool_matrix` tests (they're all `@slow`)

#### Manual Verification
- [ ] Total real session count for `make test-e2e-tools` with all runtimes is ≤12
- [ ] Total cost for a full `make test-e2e-tools` run is observed under $0.20

**Gate**: Phase 5 complete when the matrix runs clean against a real Fairy deployment with all 4 runtimes configured.

---

## Phase 6: Markers, Makefile, docs

### Overview

Register the `tool_matrix` pytest marker. Add a `make test-e2e-tools` target. Update `README.md` with the per-runtime enforcement capability table so users know what's enforceable on each runtime.

### Changes Required

#### 1. `pyproject.toml`

```toml
[tool.pytest.ini_options]
markers = [
    "slow: spawns real agent sessions (expensive)",
    "tool_matrix: verifies agent tool enforcement across runtimes",
]
```

#### 2. `Makefile`

```makefile
test-e2e-tools:
	FAIRY_API_URL=$${FAIRY_API_URL:-http://localhost:8777} \
	  uv run pytest tests/e2e/test_agent_tools.py -v -m "tool_matrix"
```

#### 3. `README.md` — add a "Tool enforcement per runtime" section

```markdown
## Tool enforcement per runtime

Fairy's `Agent.tools` field accepts the Anthropic Managed Agents `agent_toolset_20260401`
spec. Enforcement depends on what the underlying runtime CLI supports:

| Canonical tool | claude / claude-oauth | codex | gemini |
|---------------|-----------------------|-------|--------|
| `bash`        | enforceable           | not enforceable (always available) | enforceable |
| `read`        | enforceable           | not enforceable                    | enforceable |
| `write`       | enforceable           | enforceable via read-only sandbox (only when `edit` is also disabled) | enforceable (applies to both `write_file` and `replace`) |
| `edit`        | enforceable           | not enforceable                    | enforceable |
| `glob`        | enforceable           | not enforceable                    | enforceable |
| `grep`        | enforceable           | not enforceable                    | enforceable |
| `web_fetch`   | enforceable           | no codex equivalent (no-op)        | enforceable |
| `web_search`  | enforceable           | enforceable                        | enforceable |

Configurations are accepted silently even when unenforceable on the selected runtime.
For full behavioral guarantees, use the `claude` or `gemini` runtimes; `codex` is a
best-effort match.
```

### Success Criteria

#### Automated Verification
- [ ] `pytest --markers` lists both `slow` and `tool_matrix`
- [ ] `make test-e2e-tools` exists and is runnable
- [ ] `make lint` passes
- [ ] `make test` passes

#### Manual Verification
- [ ] README capability table renders correctly on GitHub
- [ ] Table matches the gap list in `thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md`

---

## Testing Strategy

### Automated
- **Unit**: every Phase 2–4 translation has exhaustive parametrized unit tests in `tests/test_sprites_exec.py`. Zero dollar cost.
- **Integration (wrapper generation)**: unit tests assert flags/files appear in generated wrapper scripts without spawning Sprites.
- **E2E**: 11 real sessions per full run, opt-in via `make test-e2e-tools`. Default `make test-e2e-fast` skips them.

### Manual Testing Steps
1. Fresh Fairy dev server. Create claude agent with `tools: [{type: "agent_toolset_20260401", default_config: {enabled: false}}]`.
2. Spawn a session asking it to read any file. Confirm session completes but the stream contains no `Read` tool_use.
3. Repeat with gemini runtime and `bash` deny — confirm Policy Engine denial appears as `tool_result.error.message` containing "denied by policy".
4. Repeat with codex runtime and `web_search: false` — confirm no `web_search` item in JSONL stream.
5. Multi-turn: create a claude session, let it complete, `POST /sessions/{id}/prompt` with a prompt that needs the denied tool. Confirm the restriction still holds.
6. Verify creating an agent with `tools: [{type: "custom", name: "x"}]` returns 422.

## Performance Considerations

- Wrapper-script size grows by at most one TOML file (<1KB) — negligible.
- No extra Sprite commands or HTTP roundtrips at session start.
- Test matrix is bounded: 11 sessions per full run, ~$0.12 at cheapest models.

## References

- Research: `thoughts/research/2026-04-16-agent-tool-enforcement-e2e.md`
- Existing e2e patterns: `tests/e2e/test_sessions.py:21-47`
- MCP wiring template: `src/fairy/sprites_exec.py:120-206` (`_build_mcp_claude`/`_codex`/`_gemini`)
- Managed Agents spec: per user-provided docs (canonical tool list: `bash, read, write, edit, glob, grep, web_fetch, web_search`)
