"""Sprite provisioning — orchestrator that creates a Sprite and dispatches to
the per-stage helpers in `provisioning_stages.py`.

Each `sprite.command()` round trip costs ~5s of WebSocket-layer overhead
regardless of what it runs, so the provisioning flow is shaped to minimize
the number of commands. See `provisioning_stages.py` for the per-stage I/O
and `provision_script.py` for the combined bash script that folds the
expensive shell work into a single round trip.
"""

from __future__ import annotations

import logging

from sprites import Sprite, SpriteError

from agent_on_demand.observability import get_tracer

from .client import best_effort_delete, require_client
from .errors import ProvisionError
# Re-exported so call-sites in `provision_session` resolve through this
# module's namespace — that lets tests patch e.g. `provisioning.install_runtime`
# to inject failures without reaching into `provisioning_stages` directly.
from .provisioning_stages import (
    apply_network_policy,
    install_runtime,
    run_provision_setup,
    write_env_file,
    write_git_credentials,
    write_runtime_config,
    write_skills,
)
from .specs import SessionSpec
from .stage_events import STAGE_CREATE_SPRITE, stage_timer

logger = logging.getLogger(__name__)


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
            install_runtime(sprite, spec, session_id)
            apply_network_policy(sprite, env, session_id)
            write_env_file(sprite, spec, session_id)
            write_git_credentials(sprite, spec.repos, session_id)
            run_provision_setup(sprite, spec, session_id)
            write_runtime_config(sprite, spec, session_id)
            write_skills(sprite, spec, session_id)
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
