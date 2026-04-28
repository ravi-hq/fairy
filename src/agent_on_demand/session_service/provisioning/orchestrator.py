"""Session provisioning — orchestrator that creates a backend handle and dispatches
to the per-stage helpers in `stages.py`.

Each backend `make_command()` round trip costs ~5s of WebSocket-layer overhead
regardless of what it runs, so the provisioning flow is shaped to minimize
the number of commands. See `stages.py` for the per-stage I/O and `script.py`
for the combined bash script that folds the expensive shell work into a
single round trip.
"""

from __future__ import annotations

import logging

from agent_on_demand.observability import get_tracer

from agent_on_demand.session_service.backends import (
    BackendError,
    SessionHandle,
    SpriteError,
)
from agent_on_demand.session_service.client import best_effort_delete, require_client
from agent_on_demand.session_service.errors import ProvisionError
from agent_on_demand.session_service.specs import SessionSpec

from .events import STAGE_CREATE_SPRITE, stage_timer

# Defensive catch in the `provision_session` `except` below: stages now route
# all backend I/O through the `SessionHandle` Protocol (which translates
# `SpriteError` → `BackendError` at the adapter boundary), so a `SpriteError`
# should never leak this far. The catch is kept because a test patches one of
# the stage helpers to raise `SpriteError` directly and asserts that the
# orchestrator still tags the failure as `ProvisionError(stage="unknown")`
# *and* triggers `best_effort_delete` — without that, an unwrapped helper
# leak would orphan a Sprite.

# Re-exported so call-sites in `provision_session` resolve through this
# module's namespace — that lets tests patch e.g. `orchestrator.install_runtime`
# to inject failures without reaching into `stages` directly.
from .stages import (
    apply_network_policy,
    install_runtime,
    run_provision_setup,
    write_env_file,
    write_git_credentials,
    write_runtime_config,
    write_skills,
)

logger = logging.getLogger(__name__)


def provision_session(user, spec: SessionSpec, session_id: str | None = None) -> SessionHandle:
    """Create a session handle on the backend and run all setup stages against it.

    On any failure the handle is best-effort destroyed before a `ProvisionError`
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
        client = require_client(user, spec.backend)
        try:
            with stage_timer(session_id, STAGE_CREATE_SPRITE):
                try:
                    handle = client.provision(spec.name)
                except BackendError as e:
                    raise ProvisionError(
                        f"Failed to create Sprite: {e}", stage=STAGE_CREATE_SPRITE
                    ) from e
        except ProvisionError as e:
            span.set_attribute("aod.failure_stage", e.stage)
            raise

        try:
            install_runtime(handle, spec, session_id)
            apply_network_policy(handle, env, session_id)
            write_env_file(handle, spec, session_id)
            write_git_credentials(handle, spec.repos, session_id)
            run_provision_setup(handle, spec, session_id)
            write_runtime_config(handle, spec, session_id)
            write_skills(handle, spec, session_id)
        except ProvisionError as e:
            span.set_attribute("aod.failure_stage", e.stage)
            best_effort_delete(client, spec.name)
            raise
        except (BackendError, SpriteError) as e:
            span.set_attribute("aod.failure_stage", "unknown")
            best_effort_delete(client, spec.name)
            raise ProvisionError(f"Failed to prepare Sprite: {e}", stage="unknown") from e

        return handle


def resume_session(user, handle: str) -> SessionHandle:
    """Look up the backend handle backing an existing session."""
    from agent_on_demand.session_service.errors import SessionHandleNotFound

    client = require_client(user)
    try:
        return client.get(handle)
    except BackendError as e:
        raise SessionHandleNotFound(f"Sprite not found: {e}") from e


def destroy_session(user, handle: str) -> None:
    """Destroy the backend session. Best-effort — logs on failure but never raises."""
    if not handle:
        return
    from agent_on_demand.session_service.client import get_client

    client = get_client(user)
    if client is None:
        logger.warning("Cannot delete handle %s: no backend credentials for user %s", handle, user)
        return
    best_effort_delete(client, handle)
