from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

from fairy.runtimes import RuntimeConfig


@dataclass(frozen=True)
class EnvironmentSetup:
    """Container environment configuration extracted from an Environment model."""
    packages: dict[str, list[str]]
    env_vars: dict[str, str]
    setup_script: str


@dataclass(frozen=True)
class RepoSpec:
    url: str
    mount_path: str
    token: str | None = None


@dataclass(frozen=True)
class McpServerSpec:
    """Normalized MCP server config, translated to runtime-specific format."""
    name: str
    type: str = "url"  # "url" or "stdio"
    # For type: "url"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    # For type: "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolsetRules:
    """Normalized mcp_toolset restrictions for a single server.

    default_enabled: default for tools not listed in per_tool (True = allow-by-default).
    per_tool: {tool_name: enabled} — overrides default for specific tools.
    """
    default_enabled: bool
    per_tool: dict[str, bool] = field(default_factory=dict)


def _build_mcp_toolset_rules(tools: list[dict]) -> dict[str, McpToolsetRules]:
    """Extract per-server rules from mcp_toolset tool entries.

    Servers without an mcp_toolset entry are absent from the result (no restrictions).
    An entry with no default_config and no configs keeps default_enabled=True and
    empty per_tool, producing a no-op rules record that runtime writers may skip.
    """
    rules: dict[str, McpToolsetRules] = {}
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "mcp_toolset":
            continue
        name = t.get("mcp_server_name")
        if not name:
            continue
        default_enabled = t.get("default_config", {}).get("enabled", True)
        per_tool = {
            c["name"]: c.get("enabled", True)
            for c in t.get("configs", [])
            if isinstance(c, dict) and "name" in c
        }
        rules[name] = McpToolsetRules(default_enabled=default_enabled, per_tool=per_tool)
    return rules


PACKAGE_MANAGER_ORDER = ["apt", "cargo", "gem", "go", "npm", "pip"]


def _build_env_vars_section(env_vars: dict[str, str]) -> str:
    """Build export statements for environment variables."""
    if not env_vars:
        return ""
    lines = ["# Environment variables"]
    for key, value in sorted(env_vars.items()):
        lines.append(f"export {key}={shlex.quote(value)}")
    return "\n".join(lines)


def _build_packages_section(packages: dict[str, list[str]]) -> str:
    """Build package installation commands in alphabetical manager order."""
    if not packages:
        return ""
    lines = ["# Install packages"]
    for manager in PACKAGE_MANAGER_ORDER:
        pkgs = packages.get(manager, [])
        if not pkgs:
            continue
        quoted = " ".join(shlex.quote(p) for p in pkgs)
        if manager == "apt":
            lines.append(f"apt-get update -qq && apt-get install -y -qq {quoted}")
        elif manager == "pip":
            lines.append(f"pip install --quiet {quoted}")
        elif manager == "npm":
            lines.append(f"npm install --global --silent {quoted}")
        elif manager == "cargo":
            for pkg in pkgs:
                lines.append(f"cargo install {shlex.quote(pkg)}")
        elif manager == "gem":
            lines.append(f"gem install --silent {quoted}")
        elif manager == "go":
            for pkg in pkgs:
                lines.append(f"go install {shlex.quote(pkg)}")
    return "\n".join(lines)


def _build_setup_script_section(setup_script: str) -> str:
    """Build the custom setup script section."""
    if not setup_script.strip():
        return ""
    return f"# Custom setup\n{setup_script}"


def _build_clone_section(repos: list[RepoSpec]) -> str:
    if not repos:
        return ""

    lines = ["# Clone GitHub repositories"]

    # Set up git credential helper so tokens don't appear in process args
    cred_lines: list[str] = []
    for repo in repos:
        if repo.token:
            cred_lines.append(
                f"https://{repo.token}:x-oauth-basic@github.com"
            )
    if cred_lines:
        # Write credentials file and configure git to use it
        lines.append("cat > /tmp/.git-credentials << 'CREDENTIALS_EOF'")
        for line in cred_lines:
            lines.append(line)
        lines.append("CREDENTIALS_EOF")
        lines.append("git config --global credential.helper 'store --file=/tmp/.git-credentials'")

    for repo in repos:
        mount = shlex.quote(repo.mount_path)
        url = shlex.quote(repo.url)
        lines.append(f"git clone --depth=1 --quiet {url} {mount}")

    # Clean up credentials after all clones complete
    if cred_lines:
        lines.append("rm -f /tmp/.git-credentials")
        lines.append("git config --global --unset credential.helper")

    return "\n".join(lines)


def _build_mcp_claude(
    servers: list[McpServerSpec],
    rules: dict[str, McpToolsetRules] | None = None,  # noqa: ARG001 — Phase 4 reads this
) -> str:
    """Generate Claude MCP config JSON and write command.

    The rules parameter is threaded through for signature parity with codex/gemini,
    but Claude applies MCP allow/deny via a separate `~/.claude/settings.json`
    writer (`_tool_files_claude_mcp`), not inside this file.
    """
    config: dict[str, dict] = {}
    for s in servers:
        if s.type == "url":
            entry: dict = {"type": "http", "url": s.url}
            if s.headers:
                entry["headers"] = s.headers
            config[s.name] = entry
        elif s.type == "stdio":
            entry = {"type": "stdio", "command": s.command, "args": s.args}
            if s.env:
                entry["env"] = s.env
            config[s.name] = entry
    content = json.dumps({"mcpServers": config}, indent=2)
    return (
        "# MCP server configuration\n"
        "cat > /tmp/mcp.json << 'MCP_EOF'\n"
        f"{content}\n"
        "MCP_EOF\n"
    )


def _codex_top_level_keys(tools: list[dict]) -> list[str]:
    """Derive top-level codex config.toml keys from Managed-Agents tools.

    Only two canonical tools have per-tool codex enforcement:
    - `web_search`: top-level `web_search = "disabled"`
    - `write` + `edit` together: `sandbox_mode = "read-only"`

    bash/read/glob/grep/web_fetch have no per-tool codex equivalent and are
    accepted silently.
    """
    toolset = _find_agent_toolset(tools)
    if toolset is None:
        return []
    default_enabled = toolset.get("default_config", {}).get("enabled", True)
    configs = {c["name"]: c.get("enabled", True) for c in toolset.get("configs", [])}

    def tool_enabled(name: str) -> bool:
        return configs.get(name, default_enabled)

    lines: list[str] = []
    if not tool_enabled("web_search"):
        lines.append('web_search = "disabled"')
    if not tool_enabled("write") and not tool_enabled("edit"):
        lines.append('sandbox_mode = "read-only"')
    return lines


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
        if server_rules is not None:
            if not server_rules.default_enabled and not server_rules.per_tool:
                lines.append("enabled = false")
            else:
                allowed = [t for t, e in server_rules.per_tool.items() if e]
                denied = [t for t, e in server_rules.per_tool.items() if not e]
                if not server_rules.default_enabled and allowed:
                    allow_list = ", ".join(f'"{t}"' for t in allowed)
                    lines.append(f"enabled_tools = [{allow_list}]")
                if denied:
                    deny_list = ", ".join(f'"{t}"' for t in denied)
                    lines.append(f"disabled_tools = [{deny_list}]")
        if s.type == "url":
            lines.append(f'url = "{s.url}"')
            for key, val in s.headers.items():
                # Codex uses bearer_token_env_var for auth
                if key.lower() == "authorization" and val.startswith("Bearer "):
                    # If it's an env var reference like ${TOKEN}, extract the var name
                    token = val.removeprefix("Bearer ").strip()
                    if token.startswith("${") and token.endswith("}"):
                        lines.append(f'bearer_token_env_var = "{token[2:-1]}"')
            lines.append("required = true")
        elif s.type == "stdio":
            lines.append(f'command = "{s.command}"')
            if s.args:
                args_str = ", ".join(f'"{a}"' for a in s.args)
                lines.append(f"args = [{args_str}]")
            if s.env:
                lines.append(f"[mcp_servers.{s.name}.env]")
                for key, val in s.env.items():
                    lines.append(f'{key} = "{val}"')
        lines.append("")
    lines.append("MCP_EOF")
    return "\n".join(lines)


def _build_mcp_gemini(
    servers: list[McpServerSpec],
    rules: dict[str, McpToolsetRules] | None = None,
) -> str:
    """Generate Gemini MCP config JSON and write command.

    MCP tool allow/deny is emitted as includeTools/excludeTools on each mcpServers
    entry — not via the Policy Engine TOML that `_tool_files_gemini` writes for
    built-in tools. Policy Engine uses single-underscore `mcp_{server}_{tool}`
    naming which misparses server aliases containing underscores, so we keep MCP
    rules localized to the settings.json file that this function already writes.
    """
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
        else:
            continue

        server_rules = rules.get(s.name)
        if server_rules is not None:
            if not server_rules.default_enabled and not server_rules.per_tool:
                entry["includeTools"] = []
            else:
                allowed = [t for t, e in server_rules.per_tool.items() if e]
                denied = [t for t, e in server_rules.per_tool.items() if not e]
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


def _build_mcp_section(
    runtime_name: str,
    servers: list[McpServerSpec],
    tools: list[dict] | None = None,
    rules: dict[str, McpToolsetRules] | None = None,
) -> str:
    """Build MCP config section for the wrapper script.

    Codex folds tool-enforcement keys into the same config.toml it uses for MCP,
    so `tools` is threaded through here. Other runtimes emit tool enforcement via
    the separate `_tool_files_*` path.

    `rules` carries per-server mcp_toolset allow/deny. Codex and gemini consume it
    inside their MCP config; claude consumes it via `_tool_files_claude_mcp`.
    """
    if runtime_name == "codex":
        return _build_mcp_codex(servers, tools=tools, rules=rules)
    if not servers:
        return ""
    if runtime_name in ("claude", "claude-oauth"):
        return _build_mcp_claude(servers, rules=rules)
    if runtime_name == "gemini":
        return _build_mcp_gemini(servers, rules=rules)
    return ""


def _mcp_cmd_flags(runtime_name: str, servers: list[McpServerSpec]) -> str:
    """Return extra CLI flags needed for MCP (only Claude needs explicit flags)."""
    if not servers:
        return ""
    if runtime_name in ("claude", "claude-oauth"):
        return " --mcp-config /tmp/mcp.json --strict-mcp-config"
    return ""


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


def _find_agent_toolset(tools: list[dict]) -> dict | None:
    for t in tools:
        if t.get("type") == "agent_toolset_20260401":
            return t
    return None


def _tool_flags_claude(tools: list[dict], mcp_server_names: list[str]) -> str:
    """Translate Managed-Agents tools spec → claude CLI flags.

    default_config.enabled=False → `--tools "<enabled>"` allowlist (empty string disables all)
    default_config.enabled=True (or missing) with per-tool disabled → `--disallowedTools "..."`
    mcp_toolset entries extend the allowlist with `mcp__<server>`.
    """
    toolset = _find_agent_toolset(tools)
    mcp_refs = [
        t["mcp_server_name"] for t in tools
        if t.get("type") == "mcp_toolset" and "mcp_server_name" in t
    ]

    if toolset is None:
        return ""

    default_enabled = toolset.get("default_config", {}).get("enabled", True)
    configs = {c["name"]: c.get("enabled", True) for c in toolset.get("configs", [])}

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


_GEMINI_TOOL_NAMES: dict[str, list[str]] = {
    "bash": ["run_shell_command"],
    "read": ["read_file"],
    "write": ["write_file", "replace"],
    "edit": ["replace"],
    "glob": ["glob"],
    "grep": ["grep_search"],
    "web_fetch": ["web_fetch"],
    "web_search": ["google_web_search"],
}

GEMINI_POLICY_PATH = "/home/sprite/.gemini/policies/fairy.toml"


def _gemini_names_toml(names: list[str]) -> str:
    return "[" + ", ".join(f'"{n}"' for n in names) + "]"


def _tool_files_gemini(tools: list[dict]) -> dict[str, str]:
    """Translate Managed-Agents tools spec → Gemini Policy Engine TOML.

    Written to ~/.gemini/policies/fairy.toml; loaded on every gemini invocation
    (including --resume), so restrictions persist across multi-turn sessions.

    default_config.enabled=False → deny-all catch-all + allow-rules for enabled tools
    default_config.enabled=True  → per-tool deny rules for each disabled tool
    """
    toolset = _find_agent_toolset(tools)
    if toolset is None:
        return {}
    default_enabled = toolset.get("default_config", {}).get("enabled", True)
    configs = {c["name"]: c.get("enabled", True) for c in toolset.get("configs", [])}

    rules: list[str] = []

    if not default_enabled:
        rules.append(
            "[[rule]]\n"
            'toolName = "*"\n'
            'decision = "deny"\n'
            "priority = 1\n"
            "interactive = false"
        )
        allowed: list[str] = []
        for canonical, enabled in configs.items():
            if enabled and canonical in _GEMINI_TOOL_NAMES:
                allowed.extend(_GEMINI_TOOL_NAMES[canonical])
        if allowed:
            rules.append(
                "[[rule]]\n"
                f"toolName = {_gemini_names_toml(allowed)}\n"
                'decision = "allow"\n'
                "priority = 100\n"
                "interactive = false"
            )
    else:
        for canonical, enabled in configs.items():
            if enabled or canonical not in _GEMINI_TOOL_NAMES:
                continue
            rules.append(
                "[[rule]]\n"
                f"toolName = {_gemini_names_toml(_GEMINI_TOOL_NAMES[canonical])}\n"
                'decision = "deny"\n'
                "interactive = false"
            )

    if not rules:
        return {}
    return {GEMINI_POLICY_PATH: "\n\n".join(rules) + "\n"}


CLAUDE_SETTINGS_PATH = "/home/sprite/.claude/settings.json"


def _tool_files_claude_mcp(
    rules: dict[str, McpToolsetRules],
) -> dict[str, str]:
    """Write ~/.claude/settings.json with permissions.allow/deny for MCP tools.

    Claude `-p` mode silently ignores MCP names in `--disallowedTools` (upstream
    issue #12863). The only working deny path in headless mode is `permissions.deny`
    in a settings file. Tool names use double-underscore `mcp__<server>[__<tool>]`.
    """
    deny: list[dict[str, str]] = []
    allow: list[dict[str, str]] = []
    for server_name, r in rules.items():
        if not r.default_enabled and not r.per_tool:
            deny.append({"tool": f"mcp__{server_name}"})
            continue
        if not r.default_enabled:
            deny.append({"tool": f"mcp__{server_name}"})
            for tool_name, enabled in r.per_tool.items():
                if enabled:
                    allow.append({"tool": f"mcp__{server_name}__{tool_name}"})
        else:
            for tool_name, enabled in r.per_tool.items():
                if not enabled:
                    deny.append({"tool": f"mcp__{server_name}__{tool_name}"})

    if not deny and not allow:
        return {}

    permissions: dict[str, list] = {}
    if allow:
        permissions["allow"] = allow
    if deny:
        permissions["deny"] = deny

    return {CLAUDE_SETTINGS_PATH: json.dumps({"permissions": permissions}, indent=2) + "\n"}


def _build_tool_flags(
    runtime_name: str,
    tools: list[dict],
    mcp_server_names: list[str],
    rules: dict[str, McpToolsetRules] | None = None,
) -> tuple[str, dict[str, str]]:
    """Translate Agent.tools into runtime-specific enforcement.

    Returns (cli_flags, files):
      cli_flags — extra flags appended to the exec line (leading space if non-empty)
      files     — {absolute_path: content} to write as heredoc blocks before exec

    Codex folds its tool enforcement into the MCP config.toml instead of using
    this path — it returns ("", {}) here. See `_build_mcp_codex`.
    """
    rules = rules or {}
    if not tools and not rules:
        return "", {}
    if runtime_name in ("claude", "claude-oauth"):
        flags = _tool_flags_claude(tools, mcp_server_names)
        files = _tool_files_claude_mcp(rules)
        return flags, files
    if runtime_name == "gemini":
        return "", _tool_files_gemini(tools)
    return "", {}


def _format_tool_files_heredoc(files: dict[str, str]) -> str:
    """Render file writes as mkdir + heredoc blocks for the wrapper script."""
    if not files:
        return ""
    blocks: list[str] = ["# Tool enforcement files"]
    for path, content in files.items():
        parent = path.rsplit("/", 1)[0] if "/" in path else "."
        blocks.append(f"mkdir -p {shlex.quote(parent)}")
        blocks.append(f"cat > {shlex.quote(path)} << 'TOOLFILE_EOF'")
        blocks.append(content.rstrip("\n"))
        blocks.append("TOOLFILE_EOF")
    return "\n".join(blocks)


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
    """Build a shell script that exports the API key and runs the agent.

    Uses a wrapper script instead of passing env= on the exec call because:
    1. env= replaces the entire environment (no PATH -> binary not found)
    2. env= appears in WebSocket URL query params (API key in server logs)
    """
    cmd = config.continue_cmd if continue_session else config.cmd
    clone_section = _build_clone_section(repos or [])

    env_vars_section = ""
    packages_section = ""
    setup_section = ""
    if environment:
        env_vars_section = _build_env_vars_section(environment.env_vars)
        packages_section = _build_packages_section(environment.packages)
        setup_section = _build_setup_script_section(environment.setup_script)

    mcp_rules = _build_mcp_toolset_rules(tools or [])
    mcp_section = _build_mcp_section(
        config.name, mcp_servers or [], tools=tools, rules=mcp_rules,
    )
    mcp_flags = _mcp_cmd_flags(config.name, mcp_servers or [])

    mcp_names = [s.name for s in (mcp_servers or [])]
    tool_flags, tool_files = _build_tool_flags(
        config.name, tools or [], mcp_names, rules=mcp_rules,
    )
    tool_files_section = _format_tool_files_heredoc(tool_files)

    return f"""#!/bin/bash
set -euo pipefail
export {config.env_var}={shlex.quote(api_key)}
export PROMPT={shlex.quote(prompt)}
{env_vars_section}

# Setup working directory
cd /home/sprite
mkdir -p .gemini
if [ ! -d .git ]; then
    git init -q
    git add -A 2>/dev/null || true
    git commit -q -m "init" --allow-empty 2>/dev/null || true
fi

{packages_section}

{clone_section}

{setup_section}

{mcp_section}

{tool_files_section}

exec {cmd}{mcp_flags}{tool_flags}
"""
