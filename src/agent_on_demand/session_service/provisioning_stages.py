"""Per-stage helpers invoked by `provision_session`.

Each function corresponds to one provisioning stage and is responsible
for emitting its `stage_timer` event and wrapping any `BackendError` as a
`ProvisionError` tagged with the stage name. The orchestrator in
`provisioning.py` owns sequencing and cleanup; everything I/O lives here.
"""

from __future__ import annotations

import io

from agent_on_demand.models import Environment

from .backend import BackendError, SessionHandle
from .env_file import build_env_file_body
from .errors import ProvisionError
from .git_credentials import build_git_credentials_lines
from .network_policy import build_network_policy

# Defensive: runtime methods now route through the `SessionHandle` Protocol,
# which translates `SpriteError` to `BackendError` at the adapter boundary.
# The `SpriteError` catch is retained as a safety net — tests assert that
# a `SpriteError` raised directly by a mocked runtime impl is still tagged
# with the correct provisioning stage.
from .sprites_backend import SpriteError
from .provision_script import (
    ENV_FILE_PATH,
    GIT_CREDS_PATH,
    PROVISION_SCRIPT_PATH,
    build_provision_script,
)
from .skills_install import build_skills_install_command
from .specs import RepoSpec, SessionSpec
from .stage_events import (
    STAGE_ENV_FILE,
    STAGE_GIT_CREDENTIALS,
    STAGE_INSTALL_RUNTIME,
    STAGE_NETWORK_POLICY,
    STAGE_PROVISION_SETUP,
    STAGE_RUNTIME_CONFIG,
    STAGE_SKILLS,
    stage_timer,
)

__all__ = [
    "apply_network_policy",
    "install_runtime",
    "run_provision_setup",
    "write_env_file",
    "write_git_credentials",
    "write_runtime_config",
    "write_skills",
]


def install_runtime(handle: SessionHandle, spec: SessionSpec, session_id: str | None) -> None:
    """Run per-runtime `install` hook before the network policy locks things
    down. For pre-baked runtimes (claude/codex/gemini) this is a no-op; for
    meta-runtimes that fetch binaries, internet access is required here."""
    with stage_timer(session_id, STAGE_INSTALL_RUNTIME):
        try:
            spec.runtime.install(handle)
        except (BackendError, SpriteError) as e:
            raise ProvisionError(
                f"Failed to install runtime: {e}", stage=STAGE_INSTALL_RUNTIME
            ) from e


def apply_network_policy(
    handle: SessionHandle, env: Environment | None, session_id: str | None
) -> None:
    policy = build_network_policy(env)
    if policy is None:
        return
    with stage_timer(session_id, STAGE_NETWORK_POLICY):
        try:
            handle.apply_network_policy(policy)
        except BackendError as e:
            raise ProvisionError(
                f"Failed to prepare Sprite: {e}", stage=STAGE_NETWORK_POLICY
            ) from e


def write_env_file(handle: SessionHandle, spec: SessionSpec, session_id: str | None) -> None:
    """Write /tmp/aod-env (fs.write only — chmod happens in the provision
    script). The file is sourced (with `set -a`) by the per-turn dispatcher,
    so every line must be a valid KEY=value shell assignment.

    Precedence: user credentials first (so their env-var names are mapped
    from `CREDENTIAL_ENV_VAR`), then the session metadata, then any
    Environment.env_vars last — per-environment overrides win."""
    from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR, UserCredential

    credentials: list[tuple[str, str]] = []
    for cred in UserCredential.objects.filter(user=spec.user):
        env_name = CREDENTIAL_ENV_VAR.get(cred.kind)
        if env_name:
            credentials.append((env_name, cred.get_value()))
    body = build_env_file_body(spec, credentials)
    with stage_timer(session_id, STAGE_ENV_FILE):
        try:
            handle.workspace().write_text(ENV_FILE_PATH, body)
        except BackendError as e:
            raise ProvisionError(f"Failed to prepare Sprite: {e}", stage=STAGE_ENV_FILE) from e


def write_git_credentials(
    handle: SessionHandle, repos: list[RepoSpec], session_id: str | None
) -> None:
    """Write /tmp/.git-credentials if any repo has a token (fs.write only;
    chmod + `git config credential.helper` live in the provision script)."""
    cred_lines = build_git_credentials_lines(repos)
    if not cred_lines:
        return
    with stage_timer(session_id, STAGE_GIT_CREDENTIALS):
        try:
            handle.workspace().write_text(GIT_CREDS_PATH, "\n".join(cred_lines) + "\n")
        except BackendError as e:
            raise ProvisionError(
                f"Failed to prepare Sprite: {e}", stage=STAGE_GIT_CREDENTIALS
            ) from e


def run_provision_setup(handle: SessionHandle, spec: SessionSpec, session_id: str | None) -> None:
    """Write /tmp/aod-provision.sh and invoke it as one backend command. This
    is the single expensive round trip — everything shell-flavoured
    (chmod, package install, git clone, user setup) runs inside it."""
    script = build_provision_script(spec)
    with stage_timer(session_id, STAGE_PROVISION_SETUP):
        err_buf = io.BytesIO()
        try:
            handle.workspace().write_text(PROVISION_SCRIPT_PATH, script)
            cmd = handle.make_command("bash", "-l", PROVISION_SCRIPT_PATH)
            cmd.set_output(stdout=io.BytesIO(), stderr=err_buf)
            cmd.run()
        except BackendError as e:
            stderr_tail = err_buf.getvalue().decode("utf-8", errors="replace")[-2000:]
            detail = f"Provisioning script failed: {e}"
            if stderr_tail.strip():
                detail = f"{detail}\nstderr:\n{stderr_tail}"
            raise ProvisionError(detail, stage=STAGE_PROVISION_SETUP) from e


def write_runtime_config(
    handle: SessionHandle,
    spec: SessionSpec,
    session_id: str | None,
) -> None:
    """Delegate config writes to the runtime. Always runs at provision time —
    meta-runtimes need their config files even when there are no MCP servers.
    Individual runtimes can skip the fs write themselves when they have
    nothing to say."""
    with stage_timer(session_id, STAGE_RUNTIME_CONFIG):
        try:
            spec.runtime.write_config(handle, spec, spec.mcp_servers)
        except (BackendError, SpriteError) as e:
            raise ProvisionError(
                f"Failed to write runtime config: {e}", stage=STAGE_RUNTIME_CONFIG
            ) from e


def write_skills(
    handle: SessionHandle,
    spec: SessionSpec,
    session_id: str | None,
) -> None:
    """Materialize skills onto the backend session.

    Inline skills are written directly with ``handle.workspace()``.
    Github-source skills are installed via the
    `skills.sh <https://skills.sh>`_ CLI (``npx -y skills@latest add ...``)
    which the session has network access for at this point in provisioning.

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
                fs = handle.workspace()
                for s in inline:
                    # Inline skills must have a name (validated upstream).
                    assert s.name is not None
                    assert s.content is not None
                    dir_path = f"{root}/{s.name}"
                    fs.write_text(f"{dir_path}/SKILL.md", s.content)
            agent_id = spec.runtime.skills_sh_agent
            if github and agent_id is not None:
                for s in github:
                    assert s.source is not None
                    cmd = build_skills_install_command(s.source, agent_id, s.name)
                    handle.make_command("bash", "-lc", cmd).run()
        except BackendError as e:
            raise ProvisionError(f"Failed to write skills: {e}", stage=STAGE_SKILLS) from e
