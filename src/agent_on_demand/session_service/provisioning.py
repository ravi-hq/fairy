"""Sprite provisioning — writes all setup files via the fs API and executes
one combined bash script to do the shell work (chmod, package install, git
clone, user setup).

Each `sprite.command()` round trip costs ~5s of WebSocket-layer overhead
regardless of what it runs, so the provisioning flow is shaped to minimize
the number of commands:

  • Files go through `sprite.filesystem()` (~0.2s each).
  • All shell work (chmod, install, clone, user setup) is combined into one
    `/tmp/aod-provision.sh` script invoked with a single
    `sprite.command("bash", "-l", "/tmp/aod-provision.sh").run()`.

Stages that previously each cost a full round trip (packages.*, clone_repos,
user_setup) are now folded into the `provision_setup` stage. See
site/docs/api/streaming.md for the current event schema.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import shlex
import time
from typing import Iterator

from sprites import NetworkPolicy, PolicyRule, Sprite, SpriteError

from agent_on_demand.models import Environment
from agent_on_demand.observability import get_tracer

from .client import best_effort_delete, require_client
from .errors import ProvisionError
from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec

ENV_FILE_PATH = "/tmp/aod-env"
GIT_CREDS_PATH = "/tmp/.git-credentials"
PROVISION_SCRIPT_PATH = "/tmp/aod-provision.sh"

logger = logging.getLogger(__name__)

PACKAGE_MANAGER_ORDER = ["apt", "cargo", "gem", "go", "npm", "pip"]

# Stage names emitted both as `ProvisionError.stage` tags (server-side logging)
# and as `stage` SSE events (see site/docs/api/streaming.md). Keep in sync.
STAGE_CREATE_SPRITE = "create_sprite"
STAGE_NETWORK_POLICY = "network_policy"
STAGE_ENV_FILE = "env_file"
STAGE_GIT_CREDENTIALS = "git_credentials"
STAGE_PROVISION_SETUP = "provision_setup"
STAGE_MCP_CONFIG = "mcp_config"
STAGE_SKILLS = "skills"
STAGE_RUNTIME_START = "runtime_start"


def emit_stage_event(
    session_id: str | None,
    stage: str,
    state: str,
    duration_ms: int | None = None,
    message: str = "",
) -> None:
    """Write a stage row to AgentSessionLog. No-op if session_id is None (unit
    tests that exercise provision_session directly without a real session row)."""
    if session_id is None:
        return
    from agent_on_demand.models import AgentSessionLog

    AgentSessionLog.objects.create(
        session_id=session_id,
        kind="stage",
        stage=stage,
        state=state,
        duration_ms=duration_ms,
        data=message,
    )


@contextlib.contextmanager
def stage_timer(session_id: str | None, stage: str) -> Iterator[None]:
    """Emit `started` entering and `done` on clean exit, or `failed` (with the
    exception message) on error. `duration_ms` is attached to done/failed."""
    emit_stage_event(session_id, stage, "started")
    start = time.monotonic()
    try:
        yield
    except Exception as e:
        emit_stage_event(
            session_id,
            stage,
            "failed",
            int((time.monotonic() - start) * 1000),
            message=str(e),
        )
        raise
    else:
        emit_stage_event(
            session_id,
            stage,
            "done",
            int((time.monotonic() - start) * 1000),
        )


_SKILLS_ROOTS: dict[str, str] = {
    "claude": "/home/sprite/.claude/skills",
    "claude-oauth": "/home/sprite/.claude/skills",
    "codex": "/home/sprite/.codex/skills",
    "gemini": "/home/sprite/.gemini/skills",
}


def provision_session(user, spec: SessionSpec, session_id: str | None = None) -> Sprite:
    """Create a Sprite and run all setup stages against it.

    On any failure the Sprite is best-effort deleted before a `ProvisionError`
    is re-raised. Per-stage `stage_timer` events are emitted as rows in
    `AgentSessionLog` so clients can render provisioning progress via the SSE
    stream. Passing `session_id=None` disables emission (used by unit tests
    that don't construct a real session row).
    """
    env = spec.environment
    tracer = get_tracer()
    with tracer.start_as_current_span(
        "session.provision",
        attributes={
            "aod.runtime": spec.runtime.name,
            "aod.repo_count": len(spec.repos),
            "aod.skill_count": len(spec.skills),
            "aod.mcp_server_count": len(spec.mcp_servers),
            "aod.env_var_count": len((env.env_vars or {})) if env else 0,
            "aod.networking_type": env.networking_type if env else "none",
            "aod.has_setup_script": bool((env.setup_script or "").strip()) if env else False,
        },
    ) as span:
        client = require_client(user)
        try:
            with stage_timer(session_id, STAGE_CREATE_SPRITE):
                try:
                    sprite = client.create_sprite(spec.name)
                except SpriteError as e:
                    raise ProvisionError(
                        f"Failed to create Sprite: {e}", stage=STAGE_CREATE_SPRITE
                    ) from e
        except ProvisionError as e:
            span.set_attribute("aod.failure_stage", e.stage)
            raise

        try:
            _apply_network_policy(sprite, env, session_id)
            _write_env_file(sprite, spec, session_id)
            _write_git_credentials(sprite, spec.repos, session_id)
            _run_provision_setup(sprite, spec, session_id)
            _write_mcp_config(sprite, spec.runtime.name, spec.mcp_servers, session_id)
            _write_skills(sprite, spec.runtime.name, spec.skills, session_id)
        except ProvisionError as e:
            span.set_attribute("aod.failure_stage", e.stage)
            best_effort_delete(client, spec.name)
            raise
        except SpriteError as e:
            span.set_attribute("aod.failure_stage", "unknown")
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


def _apply_network_policy(sprite: Sprite, env: Environment | None, session_id: str | None) -> None:
    if env is None or env.networking_type != "limited":
        return
    allowed_hosts = (env.networking_config or {}).get("allowed_hosts", [])
    rules = [PolicyRule(domain=host, action="allow") for host in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    policy = NetworkPolicy(rules=rules)
    with stage_timer(session_id, STAGE_NETWORK_POLICY):
        try:
            sprite.update_network_policy(policy)
        except SpriteError as e:
            raise ProvisionError(
                f"Failed to prepare Sprite: {e}", stage=STAGE_NETWORK_POLICY
            ) from e


def _write_env_file(sprite: Sprite, spec: SessionSpec, session_id: str | None) -> None:
    """Write /tmp/aod-env (fs.write only — chmod happens in the provision
    script). The file is sourced (with `set -a`) by the per-turn dispatcher,
    so every line must be a valid KEY=value shell assignment."""
    lines: list[str] = [f"{spec.runtime.env_var}={shlex.quote(spec.api_key)}"]
    if spec.runtime_session_id:
        lines.append(f"AOD_SESSION_ID={shlex.quote(spec.runtime_session_id)}")
    env = spec.environment
    if env is not None:
        for key in sorted(env.env_vars or {}):
            lines.append(f"{key}={shlex.quote(env.env_vars[key])}")
    body = "\n".join(lines) + "\n"
    with stage_timer(session_id, STAGE_ENV_FILE):
        try:
            fs = sprite.filesystem()
            (fs / ENV_FILE_PATH.lstrip("/")).write_text(body)
        except SpriteError as e:
            raise ProvisionError(f"Failed to prepare Sprite: {e}", stage=STAGE_ENV_FILE) from e


def _write_git_credentials(sprite: Sprite, repos: list[RepoSpec], session_id: str | None) -> None:
    """Write /tmp/.git-credentials if any repo has a token (fs.write only;
    chmod + `git config credential.helper` live in the provision script)."""
    cred_lines = [f"https://{r.token}:x-oauth-basic@github.com" for r in repos if r.token]
    if not cred_lines:
        return
    with stage_timer(session_id, STAGE_GIT_CREDENTIALS):
        try:
            fs = sprite.filesystem()
            (fs / GIT_CREDS_PATH.lstrip("/")).write_text("\n".join(cred_lines) + "\n")
        except SpriteError as e:
            raise ProvisionError(
                f"Failed to prepare Sprite: {e}", stage=STAGE_GIT_CREDENTIALS
            ) from e


def _run_provision_setup(sprite: Sprite, spec: SessionSpec, session_id: str | None) -> None:
    """Write /tmp/aod-provision.sh and invoke it as one `sprite.command`. This
    is the single expensive round trip — everything shell-flavoured
    (chmod, package install, git clone, user setup) runs inside it."""
    script = _build_provision_script(spec)
    if script is None:
        # Nothing to do (no packages, no repos, no user setup, no /tmp files
        # to chmod... actually chmod on /tmp/aod-env always needs running).
        # _build_provision_script always returns non-None because chmod on
        # ENV_FILE_PATH is unconditional, so we shouldn't hit this.
        return
    with stage_timer(session_id, STAGE_PROVISION_SETUP):
        try:
            fs = sprite.filesystem()
            (fs / PROVISION_SCRIPT_PATH.lstrip("/")).write_text(script)
            err_buf = io.BytesIO()
            cmd = sprite.command("bash", "-l", PROVISION_SCRIPT_PATH)
            # Capture stderr so a failure message carries useful context
            # (apt errors, git errors, user-setup stderr). Best-effort: if
            # the Sprite SDK doesn't support the assignment, the attribute
            # is silently ignored and err_buf stays empty.
            try:
                cmd.stderr = err_buf
            except AttributeError:
                pass
            cmd.run()
        except SpriteError as e:
            stderr_tail = err_buf.getvalue().decode("utf-8", errors="replace")[-2000:]
            detail = f"Provisioning script failed: {e}"
            if stderr_tail.strip():
                detail = f"{detail}\nstderr:\n{stderr_tail}"
            raise ProvisionError(detail, stage=STAGE_PROVISION_SETUP) from e


def _build_provision_script(spec: SessionSpec) -> str:
    """Render the combined shell script invoked as the single `sprite.command`.

    Order matters:
      1. `set -e` so any step failing aborts the rest.
      2. mkdir any parent dirs for MCP/skill files written after the script.
      3. chmod the pre-written /tmp files.
      4. Install packages (apt first; managers that rely on login PATH work
         because the script is invoked with `bash -l`).
      5. Git clones.
      6. User setup script, last (runs in an env that has packages + repos).
    """
    env = spec.environment
    lines: list[str] = ["#!/bin/bash", "set -e", ""]

    # mkdir for files that get fs.written AFTER the script runs (MCP config
    # for codex/gemini, every skill dir). /home/sprite already exists, so
    # Claude's .claude.json and default skills root don't need to be created
    # here — only the per-skill directories.
    dirs_to_make = _directories_for_post_script_writes(spec)
    if dirs_to_make:
        quoted = " ".join(shlex.quote(d) for d in dirs_to_make)
        lines.append(f"mkdir -p {quoted}")
        lines.append("")

    # chmod files pre-written to /tmp. ENV_FILE_PATH always exists at this
    # point; git creds only if any repo had a token.
    lines.append(f"chmod 600 {shlex.quote(ENV_FILE_PATH)}")
    if any(r.token for r in spec.repos):
        lines.append(f"chmod 600 {shlex.quote(GIT_CREDS_PATH)}")
    lines.append("")

    # Packages
    if env and env.packages:
        for manager in PACKAGE_MANAGER_ORDER:
            pkgs = env.packages.get(manager, [])
            if not pkgs:
                continue
            for cmd in _package_commands(manager, pkgs):
                lines.append(cmd)
        lines.append("")

    # Git clones
    if spec.repos:
        if any(r.token for r in spec.repos):
            lines.append(
                f"git config --global credential.helper "
                f"{shlex.quote(f'store --file={GIT_CREDS_PATH}')}"
            )
        for repo in spec.repos:
            lines.append(
                f"git clone --depth=1 --quiet "
                f"{shlex.quote(repo.url)} {shlex.quote(repo.mount_path)}"
            )
        lines.append("")

    # User-provided setup script, last (packages and repos are in place).
    if env is not None:
        user_script = (env.setup_script or "").strip()
        if user_script:
            lines.append(user_script)
            lines.append("")

    return "\n".join(lines)


def _directories_for_post_script_writes(spec: SessionSpec) -> list[str]:
    """Which dirs need to exist before post-script fs.writes (MCP config +
    skills). `/home/sprite` is assumed to already exist."""
    dirs: list[str] = []
    if spec.mcp_servers:
        if spec.runtime.name == "codex":
            dirs.append("/home/sprite/.codex")
        elif spec.runtime.name == "gemini":
            dirs.append("/home/sprite/.gemini")
        # claude/claude-oauth write to /home/sprite/.claude.json (no mkdir).
    if spec.skills:
        root = _SKILLS_ROOTS.get(spec.runtime.name)
        if root:
            for s in spec.skills:
                dirs.append(f"{root}/{s.name}")
    return dirs


def _package_commands(manager: str, pkgs: list[str]) -> list[str]:
    """Shell command strings for a single package manager, inlined into the
    provision script. They rely on login-shell PATH (script is invoked with
    `bash -l`)."""
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


def _write_mcp_config(
    sprite: Sprite,
    runtime_name: str,
    servers: list[McpServerSpec],
    session_id: str | None,
) -> None:
    if not servers:
        return
    with stage_timer(session_id, STAGE_MCP_CONFIG):
        try:
            if runtime_name in ("claude", "claude-oauth"):
                _write_mcp_claude(sprite, servers)
            elif runtime_name == "codex":
                _write_mcp_codex(sprite, servers)
            elif runtime_name == "gemini":
                _write_mcp_gemini(sprite, servers)
        except SpriteError as e:
            raise ProvisionError(f"Failed to write MCP config: {e}", stage=STAGE_MCP_CONFIG) from e


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
    (fs / "home/sprite/.claude.json").write_text(json.dumps({"mcpServers": config}, indent=2))


def _write_mcp_codex(sprite: Sprite, servers: list[McpServerSpec]) -> None:
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


def _write_skills(
    sprite: Sprite,
    runtime_name: str,
    skills: list[SkillSpec],
    session_id: str | None,
) -> None:
    if not skills:
        return
    root = _SKILLS_ROOTS.get(runtime_name)
    if root is None:
        return
    with stage_timer(session_id, STAGE_SKILLS):
        try:
            fs = sprite.filesystem()
            for s in skills:
                dir_path = f"{root}/{s.name}"
                (fs / f"{dir_path.lstrip('/')}/SKILL.md").write_text(s.content)
        except SpriteError as e:
            raise ProvisionError(f"Failed to write skills: {e}", stage=STAGE_SKILLS) from e
