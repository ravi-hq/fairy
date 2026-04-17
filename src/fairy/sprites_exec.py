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


def _build_mcp_claude(servers: list[McpServerSpec]) -> str:
    """Generate Claude MCP config JSON and write command."""
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


def _build_mcp_codex(servers: list[McpServerSpec]) -> str:
    """Generate Codex MCP config TOML and write command."""
    lines = ["# MCP server configuration", "mkdir -p ~/.codex"]
    lines.append("cat > ~/.codex/config.toml << 'MCP_EOF'")
    for s in servers:
        lines.append(f"[mcp_servers.{s.name}]")
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


def _build_mcp_gemini(servers: list[McpServerSpec]) -> str:
    """Generate Gemini MCP config JSON and write command."""
    config: dict[str, dict] = {}
    for s in servers:
        if s.type == "url":
            entry: dict = {"httpUrl": s.url, "trust": True}
            if s.headers:
                entry["headers"] = s.headers
            config[s.name] = entry
        elif s.type == "stdio":
            entry = {"command": s.command, "args": s.args, "trust": True}
            if s.env:
                entry["env"] = s.env
            config[s.name] = entry
    content = json.dumps({"mcpServers": config}, indent=2)
    return (
        "# MCP server configuration\n"
        "cat > ~/.gemini/settings.json << 'MCP_EOF'\n"
        f"{content}\n"
        "MCP_EOF\n"
    )


def _build_mcp_section(runtime_name: str, servers: list[McpServerSpec]) -> str:
    """Build MCP config section for the wrapper script."""
    if not servers:
        return ""
    if runtime_name in ("claude", "claude-oauth"):
        return _build_mcp_claude(servers)
    elif runtime_name == "codex":
        return _build_mcp_codex(servers)
    elif runtime_name == "gemini":
        return _build_mcp_gemini(servers)
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


def _build_tool_flags(
    runtime_name: str,
    tools: list[dict],
    mcp_server_names: list[str],
) -> tuple[str, dict[str, str]]:
    """Translate Agent.tools into runtime-specific enforcement.

    Returns (cli_flags, files):
      cli_flags — extra flags appended to the exec line (leading space if non-empty)
      files     — {absolute_path: content} to write as heredoc blocks before exec

    Runtimes without a per-tool enforcement path (codex, gemini) return ("", {}).
    Subsequent PRs land those translations.
    """
    if not tools:
        return "", {}
    if runtime_name in ("claude", "claude-oauth"):
        return _tool_flags_claude(tools, mcp_server_names), {}
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

    mcp_section = _build_mcp_section(config.name, mcp_servers or [])
    mcp_flags = _mcp_cmd_flags(config.name, mcp_servers or [])

    mcp_names = [s.name for s in (mcp_servers or [])]
    tool_flags, tool_files = _build_tool_flags(config.name, tools or [], mcp_names)
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
