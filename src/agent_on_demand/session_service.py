"""Owns the Sprites client lifecycle and session orchestration.

Views, signals, and other callers should go through this module instead of
calling `sprites.*` primitives directly. All coupling to Sprites lives here,
so it can later be placed behind a Protocol without touching the call sites.
"""

import logging
import threading

from django.conf import settings
from sprites import NetworkPolicy, PolicyRule, Sprite, SpriteError, SpritesClient

from agent_on_demand.models import (
    AgentSession,
    Environment,
    SessionTurn,
    UserSpritesKey,
)
from agent_on_demand.sprites_exec import PROMPT_FILE_PATH
from agent_on_demand.stream import run_session_background

logger = logging.getLogger(__name__)


class SessionServiceError(Exception):
    """Base for all session-service failures."""


class NoSpritesKeyError(SessionServiceError):
    """Caller has not configured a Sprites API key."""


class ProvisionError(SessionServiceError):
    """Sprites rejected a provision / prepare / write operation."""


class SessionHandleNotFound(SessionServiceError):
    """The backing Sprite is no longer available."""


def get_client(user) -> SpritesClient | None:
    """Build a SpritesClient from the caller's stored token.

    Returns None when the user has no token configured.
    """
    try:
        token = user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None
    return SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)


def _require_client(user) -> SpritesClient:
    client = get_client(user)
    if client is None:
        raise NoSpritesKeyError("No Sprites API key configured")
    return client


def _environment_to_network_policy(env: Environment | None) -> NetworkPolicy | None:
    if env is None or env.networking_type != "limited":
        return None
    allowed_hosts = (env.networking_config or {}).get("allowed_hosts", [])
    rules = [PolicyRule(domain=host, action="allow") for host in allowed_hosts]
    rules.append(PolicyRule(domain="*", action="deny"))
    return NetworkPolicy(rules=rules)


def provision_session(
    user,
    *,
    name: str,
    environment: Environment | None,
    wrapper_script: str,
    prompt: str,
) -> Sprite:
    """Create a Sprite, apply the environment's network policy, and write
    the wrapper script + initial prompt.

    On any failure after `create_sprite`, the Sprite is best-effort deleted
    before the exception is re-raised as `ProvisionError`.
    """
    client = _require_client(user)
    try:
        sprite = client.create_sprite(name)
    except SpriteError as e:
        raise ProvisionError(f"Failed to create Sprite: {e}") from e

    try:
        policy = _environment_to_network_policy(environment)
        if policy is not None:
            sprite.update_network_policy(policy)

        fs = sprite.filesystem()
        (fs / "run-agent.sh").write_text(wrapper_script)
        (fs / PROMPT_FILE_PATH.lstrip("/")).write_text(prompt)
        sprite.command("chmod", "+x", "/run-agent.sh").run()
    except SpriteError as e:
        try:
            client.delete_sprite(name)
        except SpriteError:
            logger.warning("Failed to cleanup Sprite %s", name, exc_info=True)
        raise ProvisionError(f"Failed to prepare Sprite: {e}") from e

    return sprite


def resume_session(user, sprite_name: str) -> Sprite:
    """Look up the Sprite backing an existing session."""
    client = _require_client(user)
    try:
        return client.get_sprite(sprite_name)
    except SpriteError as e:
        raise SessionHandleNotFound(f"Sprite not found: {e}") from e


def write_prompt(sprite: Sprite, prompt: str) -> None:
    """Write the per-turn prompt file into an existing Sprite's filesystem."""
    try:
        fs = sprite.filesystem()
        (fs / PROMPT_FILE_PATH.lstrip("/")).write_text(prompt)
    except SpriteError as e:
        raise ProvisionError(f"Failed to prepare Sprite: {e}") from e


def destroy_session(user, sprite_name: str) -> None:
    """Delete the Sprite. Best-effort — logs on failure but never raises."""
    if not sprite_name:
        return
    client = get_client(user)
    if client is None:
        logger.warning(
            "Cannot delete Sprite %s: no Sprites key for user %s",
            sprite_name,
            user,
        )
        return
    try:
        client.delete_sprite(sprite_name)
    except SpriteError:
        logger.warning("Failed to delete Sprite %s", sprite_name, exc_info=True)


def start_turn(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    mode: str,
    timeout: float,
) -> None:
    """Spawn a daemon thread to execute one turn of the session."""
    thread = threading.Thread(
        target=run_session_background,
        args=(session, turn, sprite, mode, timeout),
        daemon=True,
    )
    thread.start()
