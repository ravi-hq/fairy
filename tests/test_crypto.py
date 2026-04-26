"""Direct tests for the field-encryption helpers in agent_on_demand.crypto."""

import pytest
from cryptography.fernet import InvalidToken
from django.test import override_settings

from agent_on_demand.crypto import decrypt, encrypt


def test_round_trip_basic():
    assert decrypt(encrypt("hello")) == "hello"


def test_round_trip_unicode():
    assert decrypt(encrypt("héllo 🔑")) == "héllo 🔑"


def test_round_trip_empty_string():
    assert decrypt(encrypt("")) == ""


def test_ciphertext_differs_from_plaintext():
    assert encrypt("secret-payload") != b"secret-payload"


def test_field_encryption_key_overrides_secret_key():
    """When FIELD_ENCRYPTION_KEY is set, it — not SECRET_KEY — drives encryption.

    Verifies by encrypting under one explicit key and asserting a different
    explicit key cannot decrypt the resulting ciphertext.
    """
    with override_settings(FIELD_ENCRYPTION_KEY="key-A-material"):
        cipher_a = encrypt("payload")
        assert decrypt(cipher_a) == "payload"

    with override_settings(FIELD_ENCRYPTION_KEY="key-B-material"):
        with pytest.raises(InvalidToken):
            decrypt(cipher_a)


def test_secret_key_used_when_field_encryption_key_unset():
    """With FIELD_ENCRYPTION_KEY unset (None), SECRET_KEY is used as the material.

    Encrypting under SECRET_KEY should NOT decrypt under an explicit
    FIELD_ENCRYPTION_KEY (proves SECRET_KEY actually got used).
    """
    with override_settings(FIELD_ENCRYPTION_KEY=None):
        cipher_secret = encrypt("payload")
        assert decrypt(cipher_secret) == "payload"

    with override_settings(FIELD_ENCRYPTION_KEY="some-other-key"):
        with pytest.raises(InvalidToken):
            decrypt(cipher_secret)
