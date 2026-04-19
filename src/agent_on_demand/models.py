import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models

from agent_on_demand.crypto import decrypt, encrypt
from agent_on_demand.runtimes import RUNTIMES, AgentModel


class APIKey(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    key_hash = models.CharField(max_length=64, unique=True, db_index=True)
    key_prefix = models.CharField(max_length=12)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_keys"

    def __str__(self):
        return f"{self.key_prefix}... ({self.name})"

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def create_key(cls, user, name, **kwargs) -> tuple["APIKey", str]:
        """Create an API key, returning (instance, raw_key).

        The raw key is only available at creation time.
        """
        raw_key = f"aod_{secrets.token_urlsafe(32)}"
        instance = cls(
            user=user,
            key_hash=cls.hash_key(raw_key),
            key_prefix=raw_key[:12],
            name=name,
            **kwargs,
        )
        instance.save()
        return instance, raw_key


class UserRuntimeKey(models.Model):
    RUNTIME_CHOICES = [(name, name) for name in RUNTIMES]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="runtime_keys"
    )
    runtime = models.CharField(max_length=32, choices=RUNTIME_CHOICES)
    encrypted_key = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_runtime_keys"
        constraints = [
            models.UniqueConstraint(fields=["user", "runtime"], name="unique_user_runtime"),
        ]

    def __str__(self):
        return f"{self.user} — {self.runtime}"

    def set_api_key(self, raw_key: str):
        self.encrypted_key = encrypt(raw_key)

    def get_api_key(self) -> str:
        return decrypt(bytes(self.encrypted_key))


class UserSpritesKey(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sprites_key"
    )
    encrypted_key = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_sprites_keys"

    def __str__(self):
        return f"{self.user} — sprites"

    def set_api_key(self, raw_key: str):
        self.encrypted_key = encrypt(raw_key)

    def get_api_key(self) -> str:
        return decrypt(bytes(self.encrypted_key))


class AgentSession(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
        ("terminated", "Terminated"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="agent_sessions"
    )
    agent = models.ForeignKey(
        "Agent", on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )
    environment = models.ForeignKey(
        "Environment", on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )
    runtime = models.CharField(max_length=32)
    prompt = models.TextField()
    sprite_name = models.CharField(max_length=100, blank=True)
    runtime_session_id = models.UUIDField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_sessions"

    def __str__(self):
        return f"{self.runtime} — {self.status} ({self.id})"


class SessionResource(models.Model):
    RESOURCE_TYPE_CHOICES = [
        ("github_repository", "GitHub Repository"),
    ]

    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name="resources")
    resource_type = models.CharField(max_length=32, choices=RESOURCE_TYPE_CHOICES)
    url = models.URLField(max_length=500)
    mount_path = models.CharField(max_length=500)
    encrypted_token = models.BinaryField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "session_resources"

    def __str__(self):
        return f"{self.resource_type}: {self.url} → {self.mount_path}"

    def set_token(self, raw_token: str):
        self.encrypted_token = encrypt(raw_token)

    def get_token(self) -> str | None:
        if not self.encrypted_token:
            return None
        return decrypt(bytes(self.encrypted_token))


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


class SessionTurn(models.Model):
    """A single prompt+response cycle within a session.

    Sessions persist a Sprite; turns are the discrete executions on that
    Sprite. Turn 1 runs the agent in 'run' mode; turn 2+ use 'continue' mode
    so the runtime CLI resumes its own conversation state.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name="turns")
    turn_number = models.PositiveIntegerField()
    prompt = models.TextField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "session_turns"
        constraints = [
            models.UniqueConstraint(
                fields=["session", "turn_number"], name="unique_session_turn_number"
            ),
        ]
        ordering = ["session", "turn_number"]

    def __str__(self):
        return f"turn {self.turn_number} of {self.session_id} ({self.status})"


class AgentSessionLog(models.Model):
    STREAM_CHOICES = [
        ("stdout", "stdout"),
        ("stderr", "stderr"),
    ]

    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name="logs")
    turn = models.ForeignKey(
        SessionTurn, on_delete=models.CASCADE, related_name="logs", null=True, blank=True
    )
    stream = models.CharField(max_length=6, choices=STREAM_CHOICES)
    data = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_session_logs"
        indexes = [
            models.Index(fields=["session", "id"]),
            models.Index(fields=["turn", "id"]),
        ]

    def __str__(self):
        return f"[{self.stream}] {self.data[:80]}"
