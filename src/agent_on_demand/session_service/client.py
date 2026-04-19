import logging

from django.conf import settings
from sprites import SpriteError, SpritesClient

from agent_on_demand.models import UserSpritesKey

from .errors import NoSpritesKeyError

logger = logging.getLogger(__name__)


def get_client(user) -> SpritesClient | None:
    """Build a SpritesClient from the caller's stored token.

    Returns None when the user has no token configured.
    """
    try:
        token = user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None
    return SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)


def require_client(user) -> SpritesClient:
    client = get_client(user)
    if client is None:
        raise NoSpritesKeyError("No Sprites API key configured")
    return client


def best_effort_delete(client: SpritesClient, name: str) -> None:
    try:
        client.delete_sprite(name)
    except SpriteError:
        logger.warning("Failed to cleanup Sprite %s", name, exc_info=True)
