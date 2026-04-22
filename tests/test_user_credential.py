import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR, UserCredential

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="credtestuser", password="testpass")


def test_credential_env_var_importable():
    assert isinstance(CREDENTIAL_ENV_VAR, dict)
    assert CREDENTIAL_ENV_VAR["provider:anthropic"] == "ANTHROPIC_API_KEY"
    assert CREDENTIAL_ENV_VAR["provider:openai"] == "OPENAI_API_KEY"
    assert CREDENTIAL_ENV_VAR["provider:google"] == "GEMINI_API_KEY"
    assert CREDENTIAL_ENV_VAR["runtime_token:claude-oauth"] == "CLAUDE_CODE_OAUTH_TOKEN"


def test_user_credential_create_and_retrieve(user):
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("test-api-key-value")
    cred.save()

    fetched = UserCredential.objects.get(user=user, kind="provider:anthropic")
    assert fetched.get_value() == "test-api-key-value"


def test_user_credential_encryption_round_trip(user):
    raw = "dummy-credential-for-testing"
    cred = UserCredential(user=user, kind="provider:openai")
    cred.set_value(raw)
    cred.save()

    fetched = UserCredential.objects.get(pk=cred.pk)
    # Stored value must not be plaintext
    assert bytes(fetched.value_encrypted) != raw.encode()
    # But decrypted value must match original
    assert fetched.get_value() == raw


def test_user_credential_uniqueness_constraint(user):
    cred1 = UserCredential(user=user, kind="provider:anthropic")
    cred1.set_value("test-value-one")
    cred1.save()

    cred2 = UserCredential(user=user, kind="provider:anthropic")
    cred2.set_value("test-value-two")
    with pytest.raises(IntegrityError):
        cred2.save()


def test_get_value_for_returns_value(user):
    cred = UserCredential(user=user, kind="runtime_token:claude-oauth")
    cred.set_value("test-oauth-value")
    cred.save()

    result = UserCredential.get_value_for(user, "runtime_token:claude-oauth")
    assert result == "test-oauth-value"


def test_get_value_for_returns_none_when_missing(user):
    result = UserCredential.get_value_for(user, "provider:google")
    assert result is None


def test_credential_str(user):
    cred = UserCredential(user=user, kind="provider:anthropic")
    assert "provider:anthropic" in str(cred)
