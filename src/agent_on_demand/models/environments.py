import uuid

from django.conf import settings
from django.db import models


class Environment(models.Model):
    NETWORKING_CHOICES = [
        ("unrestricted", "Unrestricted"),
        ("limited", "Limited"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="environments"
    )
    name = models.CharField(max_length=200)
    packages = models.JSONField(default=dict, blank=True)
    env_vars = models.JSONField(default=dict, blank=True)
    setup_script = models.TextField(blank=True, default="")
    networking_type = models.CharField(
        max_length=16, choices=NETWORKING_CHOICES, default="unrestricted"
    )
    networking_config = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "environments"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "name"],
                condition=models.Q(archived_at__isnull=True),
                name="unique_active_environment_name",
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version} ({self.id})"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class EnvironmentVersion(models.Model):
    environment = models.ForeignKey(Environment, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    packages = models.JSONField(default=dict, blank=True)
    env_vars = models.JSONField(default=dict, blank=True)
    setup_script = models.TextField(blank=True, default="")
    networking_type = models.CharField(max_length=16)
    networking_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "environment_versions"
        constraints = [
            models.UniqueConstraint(
                fields=["environment", "version"], name="unique_environment_version"
            ),
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.environment.name} v{self.version}"
