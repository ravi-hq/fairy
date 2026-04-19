import uuid

from django.conf import settings
from django.db import models

from agent_on_demand.crypto import decrypt, encrypt


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
        "fairy.Agent", on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions"
    )
    environment = models.ForeignKey(
        "fairy.Environment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
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
