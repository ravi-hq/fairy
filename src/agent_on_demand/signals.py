from django.db.models.signals import pre_delete
from django.dispatch import receiver

from agent_on_demand import session_service
from agent_on_demand.models import AgentSession


@receiver(pre_delete, sender=AgentSession)
def delete_sprite_on_session_delete(sender, instance, **kwargs):
    """Enqueue Sprite cleanup when a session is deleted, regardless of
    deletion path. Runs async so DELETE /sessions/{id} doesn't block on the
    Sprites API."""
    handle = instance.backend_handle or instance.sprite_name
    if not handle:
        return
    session_service.destroy_session_task.defer(user_id=instance.user_id, sprite_name=handle)
