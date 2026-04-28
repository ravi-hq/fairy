import hashlib
import secrets

from django.conf import settings
from django.db import models

from agent_on_demand.crypto import decrypt, encrypt


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


CREDENTIAL_ENV_VAR: dict[str, str] = {
    "provider:anthropic": "ANTHROPIC_API_KEY",
    "provider:openai": "OPENAI_API_KEY",
    "provider:google": "GEMINI_API_KEY",
    "runtime_token:claude-oauth": "CLAUDE_CODE_OAUTH_TOKEN",
}


class UserCredential(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credentials"
    )
    kind = models.CharField(
        max_length=64
    )  # e.g. "provider:anthropic", "runtime_token:claude-oauth"
    value_encrypted = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_credentials"
        constraints = [
            models.UniqueConstraint(fields=["user", "kind"], name="unique_user_credential_kind"),
        ]

    def __str__(self):
        return f"{self.user} — {self.kind}"

    def set_value(self, raw_value: str):
        self.value_encrypted = encrypt(raw_value)

    def get_value(self) -> str:
        return decrypt(bytes(self.value_encrypted))

    @classmethod
    def get_value_for(cls, user, kind: str) -> str | None:
        try:
            return cls.objects.get(user=user, kind=kind).get_value()
        except cls.DoesNotExist:
            return None


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


class UserBackendCredential(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="backend_credentials",
    )
    backend = models.CharField(max_length=32)
    encrypted_token = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_backend_credentials"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "backend"], name="unique_user_backend_credential"
            ),
        ]

    def __str__(self):
        return f"{self.user} — {self.backend}"

    def set_token(self, raw_token: str):
        self.encrypted_token = encrypt(raw_token)

    def get_token(self) -> str:
        return decrypt(bytes(self.encrypted_token))
