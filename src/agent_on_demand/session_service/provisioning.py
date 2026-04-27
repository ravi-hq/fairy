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
import logging
import shlex
import time
from typing import Iterator

from sprites import NetworkPolicy, PolicyRule, Sprite, SpriteError

from agent_on_demand.models import Environment
from agent_on_demand.observability import get_tracer

from .client import best_effort_delete, require_client
from .errors import ProvisionError
from .provision_script import (
    ENV_FILE_PATH,
    GIT_CREDS_PATH,
    PROVISION_SCRIPT_PATH,
    build_provision_script,
)
from .specs import RepoSpec, SessionSpec

logger = logging.getLogger(__name__)

# Stage names emitted both as `ProvisionError.stage` tags (server-side logging)
# and as `stage` SSE events (see site/docs/api/streaming.md). Keep in sync.
STAGE_CREATE_SPRITE = "create_sprite"
STAGE_INSTALL_RUNTIME = "install_runtime"
STAGE_NETWORK_POLICY = "network_policy"
STAGE_ENV_FILE = "env_file"
STAGE_GIT_CREDENTIALS = "git_credentials"
STAGE_PROVISION_SETUP = "provision_setup"
STAGE_RUNTIME_CONFIG = "runtime_config"
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
            _install_runtime(sprite, spec, session_id)
            _apply_network_policy(sprite, env, session_id)
            _write_env_file(sprite, spec, session_id)
            _write_git_credentials(sprite, spec.repos, session_id)
            _run_provision_setup(sprite, spec, session_id)
            _write_runtime_config(sprite, spec, session_id)
            _write_skills(sprite, spec, session_id)
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


def _install_runtime(sprite: Sprite, spec: SessionSpec, session_id: str | None) -> None:
    """Run per-runtime `install` hook before the network policy locks things
    down. For pre-baked runtimes (claude/codex/gemini) this is a no-op; for
    meta-runtimes that fetch binaries, internet access is required here."""
    with stage_timer(session_id, STAGE_INSTALL_RUNTIME):
        try:
            spec.runtime.install(sprite)
        except SpriteError as e:
            raise ProvisionError(
                f"Failed to install runtime: {e}", stage=STAGE_INSTALL_RUNTIME
            ) from e


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
    so every line must be a valid KEY=value shell assignment.

    Precedence: user credentials first (so their env-var names are mapped
    from `CREDENTIAL_ENV_VAR`), then the session metadata, then any
    Environment.env_vars last — per-environment overrides win."""
    from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR, UserCredential

    from .env_file import build_env_file_body

    credentials: list[tuple[str, str]] = []
    for cred in UserCredential.objects.filter(user=spec.user):
        env_name = CREDENTIAL_ENV_VAR.get(cred.kind)
        if env_name:
            credentials.append((env_name, cred.get_value()))
    body = build_env_file_body(spec, credentials)
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
    script = build_provision_script(spec)
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


def _write_runtime_config(
    sprite: Sprite,
    spec: SessionSpec,
    session_id: str | None,
) -> None:
    """Delegate config writes to the runtime. Always runs at provision time —
    meta-runtimes need their config files even when there are no MCP servers.
    Individual runtimes can skip the fs write themselves when they have
    nothing to say."""
    with stage_timer(session_id, STAGE_RUNTIME_CONFIG):
        try:
            spec.runtime.write_config(sprite, spec, spec.mcp_servers)
        except SpriteError as e:
            raise ProvisionError(
                f"Failed to write runtime config: {e}", stage=STAGE_RUNTIME_CONFIG
            ) from e


def _write_skills(
    sprite: Sprite,
    spec: SessionSpec,
    session_id: str | None,
) -> None:
    """Materialize skills onto the Sprite.

    Inline skills are written directly with ``sprite.filesystem()``.
    Github-source skills are installed via the
    `skills.sh <https://skills.sh>`_ CLI (``npx -y skills@latest add ...``)
    which the Sprite has network access for at this point in provisioning.

    Runtimes whose ``skills_root`` is ``None`` skip inline writes; runtimes
    without a ``skills_sh_agent`` skip github installs.
    """
    if not spec.skills:
        return
    inline = [s for s in spec.skills if s.content is not None]
    github = [s for s in spec.skills if s.source is not None]

    with stage_timer(session_id, STAGE_SKILLS):
        try:
            root = spec.runtime.skills_root
            if inline and root is not None:
                fs = sprite.filesystem()
                for s in inline:
                    # Inline skills must have a name (validated upstream).
                    assert s.name is not None
                    assert s.content is not None
                    dir_path = f"{root}/{s.name}"
                    (fs / f"{dir_path.lstrip('/')}/SKILL.md").write_text(s.content)
            agent_id = spec.runtime.skills_sh_agent
            if github and agent_id is not None:
                for s in github:
                    assert s.source is not None
                    cmd = (
                        f"npx -y skills@latest add {shlex.quote(s.source)} "
                        f"--global --agent {shlex.quote(agent_id)} --yes"
                    )
                    if s.name:
                        # Pin to a single skill from the repo. Without --skill,
                        # the CLI installs every SKILL.md it finds.
                        cmd += f" --skill {shlex.quote(s.name)}"
                    sprite.command("bash", "-lc", cmd).run()
        except SpriteError as e:
            raise ProvisionError(f"Failed to write skills: {e}", stage=STAGE_SKILLS) from e
