from django.db.models.signals import pre_delete
from django.dispatch import receiver

from agent_on_demand import session_service
from agent_on_demand.models import AgentSession


@receiver(pre_delete, sender=AgentSession)
def delete_sprite_on_session_delete(sender, instance, **kwargs):
    """Clean up the Sprite container when a session is deleted, regardless of deletion path."""
    session_service.destroy_session(instance.user, instance.sprite_name)
