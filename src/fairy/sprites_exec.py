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
class SkillSpec:
    """A SKILL.md file to materialize onto the Sprite filesystem.

    `content` is the full SKILL.md text including YAML frontmatter. `name`
    is the directory slug, validated upstream to match [a-z0-9][a-z0-9-]{0,63}.
    """
    name: str
    content: str


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


_SKILLS_ROOTS: dict[str, str] = {
    "claude": "/home/sprite/.claude/skills",
    "claude-oauth": "/home/sprite/.claude/skills",
    "codex": "/home/sprite/.codex/skills",
    "gemini": "/home/sprite/.gemini/skills",
}


def _build_skills_section(runtime_name: str, skills: list[SkillSpec]) -> str:
    """Emit shell commands that write each SKILL.md into the runtime's skills dir.

    Each skill becomes <root>/<name>/SKILL.md. Content is written via a
    single-quoted heredoc so it is emitted verbatim — no variable expansion.
    The literal string ``SKILL_EOF`` is rejected upstream in the validator to
    prevent heredoc-closure injection.
    """
    if not skills:
        return ""
    root = _SKILLS_ROOTS.get(runtime_name)
    if root is None:
        return ""
    lines = ["# Agent skills"]
    for s in skills:
        dir_path = f"{root}/{s.name}"
        lines.append(f"mkdir -p {shlex.quote(dir_path)}")
        lines.append(f"cat > {shlex.quote(dir_path + '/SKILL.md')} << 'SKILL_EOF'")
        lines.append(s.content)
        lines.append("SKILL_EOF")
    return "\n".join(lines)


def build_wrapper_script(
    config: RuntimeConfig,
    api_key: str,
    prompt: str,
    *,
    continue_session: bool = False,
    repos: list[RepoSpec] | None = None,
    environment: EnvironmentSetup | None = None,
    mcp_servers: list[McpServerSpec] | None = None,
    skills: list[SkillSpec] | None = None,
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
    skills_section = _build_skills_section(config.name, skills or [])

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

{skills_section}

exec {cmd}{mcp_flags}
"""
