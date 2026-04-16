import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models

from fairy.crypto import decrypt, encrypt
from fairy.runtimes import RUNTIMES


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
        raw_key = f"fairy_{secrets.token_urlsafe(32)}"
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


class AgentSession(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("running", "Running"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    runtime = models.CharField(max_length=32)
    prompt = models.TextField()
    sprite_name = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending")
    exit_code = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_sessions"

    def __str__(self):
        return f"{self.runtime} — {self.status} ({self.id})"


class AgentSessionLog(models.Model):
    STREAM_CHOICES = [
        ("stdout", "stdout"),
        ("stderr", "stderr"),
    ]

    session = models.ForeignKey(
        AgentSession, on_delete=models.CASCADE, related_name="logs"
    )
    stream = models.CharField(max_length=6, choices=STREAM_CHOICES)
    data = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_session_logs"
        indexes = [
            models.Index(fields=["session", "id"]),
        ]

    def __str__(self):
        return f"[{self.stream}] {self.data[:80]}"
