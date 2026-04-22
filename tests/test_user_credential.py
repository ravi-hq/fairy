import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR, UserCredential

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="credtestuser", password="tp")


def test_credential_env_var_importable():
    assert isinstance(CREDENTIAL_ENV_VAR, dict)
    expected_kinds = {
        "provider:anthropic",
        "provider:openai",
        "provider:google",
        "runtime_token:claude-oauth",
    }
    assert set(CREDENTIAL_ENV_VAR.keys()) == expected_kinds
    # Each kind maps to a non-empty uppercase env var name
    for kind, env_var in CREDENTIAL_ENV_VAR.items():
        assert env_var, f"env var for {kind!r} should not be empty"
        assert env_var == env_var.upper(), f"env var {env_var!r} should be uppercase"


def test_user_credential_create_and_retrieve(user):
    cred = UserCredential(user=user, kind="provider:anthropic")
    cred.set_value("hello-world-value")
    cred.save()

    fetched = UserCredential.objects.get(user=user, kind="provider:anthropic")
    assert fetched.get_value() == "hello-world-value"


def test_user_credential_encryption_round_trip(user):
    raw = "round-trip-test-value"
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
    cred1.set_value("first-value")
    cred1.save()

    cred2 = UserCredential(user=user, kind="provider:anthropic")
    cred2.set_value("second-value")
    with pytest.raises(IntegrityError):
        cred2.save()


def test_get_value_for_returns_value(user):
    cred = UserCredential(user=user, kind="runtime_token:claude-oauth")
    cred.set_value("stored-value")
    cred.save()

    result = UserCredential.get_value_for(user, "runtime_token:claude-oauth")
    assert result == "stored-value"


def test_get_value_for_returns_none_when_missing(user):
    result = UserCredential.get_value_for(user, "provider:google")
    assert result is None


def test_credential_str(user):
    cred = UserCredential(user=user, kind="provider:anthropic")
    assert "provider:anthropic" in str(cred)
