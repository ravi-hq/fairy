import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from sprites import SpritesClient, SpriteError

from fairy.models import AgentSession

logger = logging.getLogger(__name__)


def _get_client() -> SpritesClient:
    from django.conf import settings

    return SpritesClient(
        token=settings.SPRITES_TOKEN,
        base_url=settings.SPRITES_BASE_URL,
    )


@receiver(pre_delete, sender=AgentSession)
def delete_sprite_on_session_delete(sender, instance, **kwargs):
    """Clean up the Sprite container when a session is deleted, regardless of deletion path."""
    if not instance.sprite_name:
        return
    try:
        client = _get_client()
        client.delete_sprite(instance.sprite_name)
    except SpriteError:
        logger.warning("Failed to delete Sprite %s", instance.sprite_name, exc_info=True)
