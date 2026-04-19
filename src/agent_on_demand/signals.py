import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from sprites import SpritesClient, SpriteError

from agent_on_demand.models import AgentSession, UserSpritesKey

logger = logging.getLogger(__name__)


def _get_client_for(user) -> SpritesClient | None:
    from django.conf import settings

    try:
        token = user.sprites_key.get_api_key()
    except UserSpritesKey.DoesNotExist:
        return None
    return SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)


@receiver(pre_delete, sender=AgentSession)
def delete_sprite_on_session_delete(sender, instance, **kwargs):
    """Clean up the Sprite container when a session is deleted, regardless of deletion path."""
    if not instance.sprite_name:
        return
    client = _get_client_for(instance.user)
    if client is None:
        logger.warning(
            "Cannot delete Sprite %s on session delete: no Sprites key for user %s",
            instance.sprite_name,
            instance.user,
        )
        return
    try:
        client.delete_sprite(instance.sprite_name)
    except SpriteError:
        logger.warning("Failed to delete Sprite %s", instance.sprite_name, exc_info=True)
