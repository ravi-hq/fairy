from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from agent_on_demand.runtimes import RuntimeConfig


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
    """Build package installation commands in alphabetical manager order.

    Install output is deliberately NOT silenced — when a package install fails
    with `set -e`, the trailing stderr is the operator's only diagnostic.
    `apt-get update` keeps `-qq` because its normal output is noise, not signal;
    errors there still propagate via exit code.
    """
    if not packages:
        return ""
    lines = ["# Install packages"]
    for manager in PACKAGE_MANAGER_ORDER:
        pkgs = packages.get(manager, [])
        if not pkgs:
            continue
        quoted = " ".join(shlex.quote(p) for p in pkgs)
        if manager == "apt":
            lines.append(f"apt-get update -qq && apt-get install -y {quoted}")
        elif manager == "pip":
            lines.append(f"pip install {quoted}")
        elif manager == "npm":
            lines.append(f"npm install --global {quoted}")
        elif manager == "cargo":
            for pkg in pkgs:
                lines.append(f"cargo install {shlex.quote(pkg)}")
        elif manager == "gem":
            lines.append(f"gem install {quoted}")
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

    # Credentials file is unlinked by the EXIT trap, so we don't repeat the rm here.
    cred_lines: list[str] = []
    for repo in repos:
        if repo.token:
            cred_lines.append(f"https://{repo.token}:x-oauth-basic@github.com")
    if cred_lines:
        lines.append("cat > /tmp/.git-credentials << 'CREDENTIALS_EOF'")
        for line in cred_lines:
            lines.append(line)
        lines.append("CREDENTIALS_EOF")
        lines.append("git config --global credential.helper 'store --file=/tmp/.git-credentials'")

    for repo in repos:
        mount = shlex.quote(repo.mount_path)
        url = shlex.quote(repo.url)
        lines.append(f"git clone --depth=1 --quiet {url} {mount}")

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
    return f"# MCP server configuration\ncat > /tmp/mcp.json << 'MCP_EOF'\n{content}\nMCP_EOF\n"


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


PROMPT_FILE_PATH = "/tmp/aod-prompt.txt"
INIT_SENTINEL_PATH = "/tmp/aod-initialized"

_TEMPLATE_PATH = Path(__file__).parent / "run_agent.sh.tmpl"


def build_wrapper_script(
    config: RuntimeConfig,
    api_key: str,
    *,
    runtime_session_id: str | None = None,
    repos: list[RepoSpec] | None = None,
    environment: EnvironmentSetup | None = None,
    mcp_servers: list[McpServerSpec] | None = None,
    skills: list[SkillSpec] | None = None,
) -> str:
    """Build a mode-dispatching shell script for a session.

    Contract with ``session_service``:

    * The script is uploaded to ``/run-agent.sh`` once at session-create time
      and never re-uploaded.
    * Before every invocation, the caller writes the per-turn prompt to
      ``{PROMPT_FILE_PATH}``.
    * Invocation form: ``bash /run-agent.sh <mode>`` where ``<mode>`` is
      ``run`` on turn 1 and ``continue`` on subsequent turns.
    * Non-idempotent setup (packages, clone, user setup_script, git init) is
      gated behind ``{INIT_SENTINEL_PATH}`` so it runs exactly once — safe
      if ``continue`` is invoked first or ``run`` is re-invoked.
    * MCP config and skill files are rewritten on every invocation; they're
      idempotent and runtimes read them at startup.
    * On any failure, the trap ``ERR`` handler writes a single
      ``AOD_STAGE_FAILED: ...`` marker to stderr identifying the failing
      command, then the script exits non-zero.
    * On exit (success or failure), the trap ``EXIT`` handler clears
      ``/tmp/.git-credentials`` and unsets the git credential helper.

    The wrapper exists instead of invoking the runtime CLI directly because:

    1. ``env=`` on the Sprites command replaces the entire environment (so
       ``PATH`` is empty and the runtime binary can't be found).
    2. ``env=`` leaks into the WebSocket URL query string, which lands API
       keys in server-side logs.
    """
    template = _TEMPLATE_PATH.read_text()

    api_key_export = f"export {config.env_var}={shlex.quote(api_key)}"
    session_id_export = (
        f"export AOD_SESSION_ID={shlex.quote(runtime_session_id)}" if runtime_session_id else ""
    )
    env_vars_section = ""
    packages_section = ""
    setup_section = ""
    if environment:
        env_vars_section = _build_env_vars_section(environment.env_vars)
        packages_section = _build_packages_section(environment.packages)
        setup_section = _build_setup_script_section(environment.setup_script)

    clone_section = _build_clone_section(repos or [])
    mcp_section = _build_mcp_section(config.name, mcp_servers or [])
    mcp_flags = _mcp_cmd_flags(config.name, mcp_servers or [])
    skills_section = _build_skills_section(config.name, skills or [])

    first_run_body = "\n\n".join(
        s for s in (packages_section, clone_section, setup_section) if s
    )

    replacements = {
        "@@API_KEY_EXPORT@@": api_key_export,
        "@@SESSION_ID_EXPORT@@": session_id_export,
        "@@ENV_VARS_BLOCK@@": env_vars_section,
        "@@PROMPT_FILE_PATH@@": shlex.quote(PROMPT_FILE_PATH),
        "@@PROMPT_FILE_PATH_RAW@@": PROMPT_FILE_PATH,
        "@@INIT_SENTINEL_PATH@@": shlex.quote(INIT_SENTINEL_PATH),
        "@@FIRST_RUN_BODY@@": first_run_body,
        "@@MCP_SECTION@@": mcp_section,
        "@@SKILLS_SECTION@@": skills_section,
        "@@RUN_CMD@@": f"{config.cmd}{mcp_flags}",
        "@@CONTINUE_CMD@@": f"{config.continue_cmd}{mcp_flags}",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)

    # Collapse the blank lines left behind by empty sections so the rendered
    # script is readable. At most one blank line between sections.
    lines = template.splitlines()
    collapsed: list[str] = []
    for line in lines:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed) + "\n"
