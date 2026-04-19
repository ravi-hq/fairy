"""Stage-by-stage Sprite provisioning.

Each stage is its own `sprite.command()` or filesystem write, so a failure in
any stage surfaces with its own exit code and stderr. Errors are wrapped as
`ProvisionError(stage=...)` for server-side logging; the stage tag is never
included in API responses.
"""

from __future__ import annotations

import json
import logging
import shlex

from sprites import NetworkPolicy, PolicyRule, Sprite, SpriteError

from agent_on_demand.models import Environment

from .client import best_effort_delete, require_client
from .dispatcher import ENV_FILE_PATH, RUN_SCRIPT_PATH, render_dispatcher_script
from .errors import ProvisionError
from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec

logger = logging.getLogger(__name__)

PACKAGE_MANAGER_ORDER = ["apt", "cargo", "gem", "go", "npm", "pip"]

_SKILLS_ROOTS: dict[str, str] = {
    "claude": "/home/sprite/.claude/skills",
    "claude-oauth": "/home/sprite/.claude/skills",
    "codex": "/home/sprite/.codex/skills",
    "gemini": "/home/sprite/.gemini/skills",
}


def provision_session(user, spec: SessionSpec) -> Sprite:
    """Create a Sprite and run all setup stages against it.

    On any failure the Sprite is best-effort deleted before a `ProvisionError`
    is re-raised.
    """
    client = require_client(user)
    try:
        sprite = client.create_sprite(spec.name)
    except SpriteError as e:
        raise ProvisionError(f"Failed to create Sprite: {e}", stage="create") from e

    try:
        _apply_network_policy(sprite, spec.environment)
        _write_env_file(sprite, spec)
        _install_packages(sprite, spec.environment)
        _clone_repos(sprite, spec.repos)
        _run_user_setup(sprite, spec.environment)
        _write_mcp_config(sprite, spec.runtime.name, spec.mcp_servers)
        _write_skills(sprite, spec.runtime.name, spec.skills)
        _write_run_script(sprite, spec)
    except ProvisionError:
        best_effort_delete(client, spec.name)
        raise
    except SpriteError as e:
        best_effort_delete(client, spec.name)
        raise ProvisionError(f"Failed to prepare Sprite: {e}", stage="unknown") from e

    return sprite


def resume_session(user, sprite_name: str) -> Sprite:
    """Look up the Sprite backing an existing session."""
    from .errors import SessionHandleNotFound

    client = require_client(user)
    try:
        return client.get_sprite(sprite_name)
    except SpriteError as e:
        raise SessionHandleNotFound(f"Sprite not found: {e}") from e


def destroy_session(user, sprite_name: str) -> None:
    """Delete the Sprite. Best-effort — logs on failure but never raises."""
    if not sprite_name:
        return
    from .client import get_client

    client = get_client(user)
    if client is None:
        logger.warning("Cannot delete Sprite %s: no Sprites key for user %s", sprite_name, user)
        return
    best_effort_delete(client, sprite_name)


def _apply_network_policy(sprite: Sprite, env: Environment | None) -> None:
    if env is None or env.networking_type != "limited":
        return
    allowed_hosts = (env.networking_config or {}).get("allowed_hosts", [])
    rules = [PolicyRule(domain=host, action="allow") for host in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    policy = NetworkPolicy(rules=rules)
    try:
        sprite.update_network_policy(policy)
    except SpriteError as e:
        raise ProvisionError(f"Failed to prepare Sprite: {e}", stage="network_policy") from e


def _write_env_file(sprite: Sprite, spec: SessionSpec) -> None:
    """Write /tmp/aod-env holding the runtime API key, AOD_SESSION_ID, and
    Environment.env_vars. The file is sourced (with `set -a`) by the dispatcher,
    so every line must be a valid KEY=value shell assignment."""
    lines: list[str] = [f"{spec.runtime.env_var}={shlex.quote(spec.api_key)}"]
    if spec.runtime_session_id:
        lines.append(f"AOD_SESSION_ID={shlex.quote(spec.runtime_session_id)}")
    env = spec.environment
    if env is not None:
        for key in sorted(env.env_vars or {}):
            lines.append(f"{key}={shlex.quote(env.env_vars[key])}")
    body = "\n".join(lines) + "\n"
    try:
        fs = sprite.filesystem()
        (fs / ENV_FILE_PATH.lstrip("/")).write_text(body)
        sprite.command("chmod", "600", ENV_FILE_PATH).run()
    except SpriteError as e:
        raise ProvisionError(f"Failed to prepare Sprite: {e}", stage="env_file") from e


def _install_packages(sprite: Sprite, env: Environment | None) -> None:
    if env is None or not env.packages:
        return
    for manager in PACKAGE_MANAGER_ORDER:
        pkgs = env.packages.get(manager, [])
        if not pkgs:
            continue
        commands = _package_commands(manager, pkgs)
        for argv in commands:
            try:
                sprite.command("bash", "-lc", argv).run()
            except SpriteError as e:
                raise ProvisionError(
                    f"Failed to install {manager} packages: {e}",
                    stage=f"packages.{manager}",
                ) from e


def _package_commands(manager: str, pkgs: list[str]) -> list[str]:
    """Shell command strings for a single package manager. Kept as strings
    (run via `bash -lc`) because several managers rely on shell features like
    `&&` chaining and PATH resolution from the user's login shell."""
    quoted = " ".join(shlex.quote(p) for p in pkgs)
    if manager == "apt":
        return [f"apt-get update -qq && apt-get install -y {quoted}"]
    if manager == "pip":
        return [f"pip install {quoted}"]
    if manager == "npm":
        return [f"npm install --global {quoted}"]
    if manager == "cargo":
        return [f"cargo install {shlex.quote(p)}" for p in pkgs]
    if manager == "gem":
        return [f"gem install {quoted}"]
    if manager == "go":
        return [f"go install {shlex.quote(p)}" for p in pkgs]
    return []


def _clone_repos(sprite: Sprite, repos: list[RepoSpec]) -> None:
    if not repos:
        return
    cred_path = "/tmp/.git-credentials"
    cred_lines: list[str] = []
    for repo in repos:
        if repo.token:
            cred_lines.append(f"https://{repo.token}:x-oauth-basic@github.com")

    try:
        if cred_lines:
            fs = sprite.filesystem()
            (fs / cred_path.lstrip("/")).write_text("\n".join(cred_lines) + "\n")
            sprite.command("chmod", "600", cred_path).run()
            sprite.command(
                "git",
                "config",
                "--global",
                "credential.helper",
                f"store --file={cred_path}",
            ).run()
        for repo in repos:
            sprite.command("git", "clone", "--depth=1", "--quiet", repo.url, repo.mount_path).run()
    except SpriteError as e:
        raise ProvisionError(f"Failed to clone repos: {e}", stage="clone") from e
    finally:
        # Always attempt cleanup, even on clone failure. Cleanup errors are
        # logged but don't shadow the real failure.
        try:
            sprite.command("rm", "-f", cred_path).run()
            sprite.command("git", "config", "--global", "--unset", "credential.helper").run()
        except SpriteError:
            logger.warning("Failed to clean git credentials on Sprite", exc_info=True)


def _run_user_setup(sprite: Sprite, env: Environment | None) -> None:
    if env is None:
        return
    script = (env.setup_script or "").strip()
    if not script:
        return
    try:
        sprite.command("bash", "-lc", script).run()
    except SpriteError as e:
        raise ProvisionError(f"Custom setup failed: {e}", stage="user_setup") from e


def _write_mcp_config(sprite: Sprite, runtime_name: str, servers: list[McpServerSpec]) -> None:
    if not servers:
        return
    try:
        if runtime_name in ("claude", "claude-oauth"):
            _write_mcp_claude(sprite, servers)
        elif runtime_name == "codex":
            _write_mcp_codex(sprite, servers)
        elif runtime_name == "gemini":
            _write_mcp_gemini(sprite, servers)
    except SpriteError as e:
        raise ProvisionError(f"Failed to write MCP config: {e}", stage="mcp_config") from e


def _write_mcp_claude(sprite: Sprite, servers: list[McpServerSpec]) -> None:
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
    fs = sprite.filesystem()
    (fs / "tmp/mcp.json").write_text(json.dumps({"mcpServers": config}, indent=2))


def _write_mcp_codex(sprite: Sprite, servers: list[McpServerSpec]) -> None:
    sprite.command("mkdir", "-p", "/home/sprite/.codex").run()
    lines: list[str] = []
    for s in servers:
        lines.append(f"[mcp_servers.{s.name}]")
        if s.type == "url":
            lines.append(f'url = "{s.url}"')
            for key, val in s.headers.items():
                if key.lower() == "authorization" and val.startswith("Bearer "):
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
    fs = sprite.filesystem()
    (fs / "home/sprite/.codex/config.toml").write_text("\n".join(lines))


def _write_mcp_gemini(sprite: Sprite, servers: list[McpServerSpec]) -> None:
    sprite.command("mkdir", "-p", "/home/sprite/.gemini").run()
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
    fs = sprite.filesystem()
    (fs / "home/sprite/.gemini/settings.json").write_text(
        json.dumps({"mcpServers": config}, indent=2)
    )


def _write_skills(sprite: Sprite, runtime_name: str, skills: list[SkillSpec]) -> None:
    if not skills:
        return
    root = _SKILLS_ROOTS.get(runtime_name)
    if root is None:
        return
    try:
        fs = sprite.filesystem()
        for s in skills:
            dir_path = f"{root}/{s.name}"
            sprite.command("mkdir", "-p", dir_path).run()
            (fs / f"{dir_path.lstrip('/')}/SKILL.md").write_text(s.content)
    except SpriteError as e:
        raise ProvisionError(f"Failed to write skills: {e}", stage="skills") from e


def _write_run_script(sprite: Sprite, spec: SessionSpec) -> None:
    script = render_dispatcher_script(spec.runtime, has_mcp=bool(spec.mcp_servers))
    try:
        fs = sprite.filesystem()
        (fs / RUN_SCRIPT_PATH.lstrip("/")).write_text(script)
        sprite.command("chmod", "+x", RUN_SCRIPT_PATH).run()
    except SpriteError as e:
        raise ProvisionError(f"Failed to write run script: {e}", stage="run_script") from e
