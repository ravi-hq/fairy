from django.conf import settings
from django.db import models

from agent_on_demand.models.sessions import AgentSession


ACTIVE_SESSION_STATUSES = ("pending", "running")


class UserQuota(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quota"
    )
    max_concurrent_sessions = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Per-user override. Null uses settings.DEFAULT_MAX_CONCURRENT_SESSIONS.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_quotas"

    def __str__(self):
        return f"{self.user} — quota"

    @classmethod
    def active_session_count_for(cls, user) -> int:
        return AgentSession.objects.filter(user=user, status__in=ACTIVE_SESSION_STATUSES).count()
