import uuid

from django.conf import settings
from django.db import models

from agent_on_demand.models.environments import Environment
from agent_on_demand.runtimes import RUNTIMES, AgentModel


class Agent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agents"
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    system = models.TextField(blank=True, default="")
    model = models.CharField(max_length=100, choices=AgentModel.choices())
    runtime = models.CharField(max_length=32, choices=[(name, name) for name in RUNTIMES])
    environment = models.ForeignKey(
        Environment, on_delete=models.SET_NULL, null=True, blank=True, related_name="agents"
    )
    skills = models.JSONField(default=list, blank=True)
    mcp_servers = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agents"

    def __str__(self):
        return f"{self.name} v{self.version} ({self.id})"

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class AgentVersion(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    system = models.TextField(blank=True, default="")
    model = models.CharField(max_length=100)
    runtime = models.CharField(max_length=32)
    environment = models.ForeignKey(Environment, on_delete=models.SET_NULL, null=True, blank=True)
    skills = models.JSONField(default=list, blank=True)
    mcp_servers = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_versions"
        constraints = [
            models.UniqueConstraint(fields=["agent", "version"], name="unique_agent_version"),
        ]
        ordering = ["-version"]

    def __str__(self):
        return f"{self.agent.name} v{self.version}"
