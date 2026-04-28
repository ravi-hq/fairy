"""Tests for UserBackendCredential model and the backend-aware `get_client`
lookup."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import Client

from agent_on_demand.models import UserBackendCredential
from agent_on_demand.session_service.client import get_client, require_client
from agent_on_demand.session_service.errors import NoBackendCredentialsError


@pytest.fixture
def user(db):
    return User.objects.create_user(username="bccuser", password="p")


# --- Model: encryption round-trip + uniqueness ---


def test_user_backend_credential_round_trip(user):
    raw = "fake-sprites-token-abcdef"
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token(raw)
    cred.save()

    fetched = UserBackendCredential.objects.get(pk=cred.pk)
    assert bytes(fetched.encrypted_token) != raw.encode()
    assert fetched.get_token() == raw


def test_user_backend_credential_unique_user_backend(user):
    cred1 = UserBackendCredential(user=user, backend="sprites")
    cred1.set_token("first")
    cred1.save()

    cred2 = UserBackendCredential(user=user, backend="sprites")
    cred2.set_token("second")
    with pytest.raises(IntegrityError):
        cred2.save()


def test_user_backend_credential_str_includes_user_and_backend(user):
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("t")
    cred.save()
    s = str(cred)
    assert str(user) in s
    assert "sprites" in s


# --- get_client(user, backend) ---


def test_get_client_returns_none_when_no_credential(user, mocker):
    mocker.patch(
        "agent_on_demand.session_service.backends.sprites.SpritesBackend.create_client",
        return_value="dummy-client",
    )
    assert get_client(user) is None


def test_get_client_uses_user_backend_credential(user, mocker):
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("from-new-table")
    cred.save()

    create = mocker.patch(
        "agent_on_demand.session_service.backends.sprites.SpritesBackend.create_client",
        return_value="dummy-client",
    )
    result = get_client(user)

    assert result == "dummy-client"
    create.assert_called_once_with("from-new-table")


def test_get_client_unknown_backend_raises(user):
    with pytest.raises(NoBackendCredentialsError, match="Unknown backend"):
        get_client(user, backend="modal")


def test_require_client_raises_when_no_credential(user):
    with pytest.raises(NoBackendCredentialsError):
        require_client(user)


# --- Admin smoke: forms render without 500 ---


@pytest.fixture
def admin_client(db):
    admin = User.objects.create_superuser(username="adm", password="p", email="a@b.c")
    c = Client()
    c.force_login(admin)
    return c


def test_admin_user_backend_credential_changelist(admin_client):
    resp = admin_client.get("/admin/fairy/userbackendcredential/")
    assert resp.status_code == 200


def test_admin_user_backend_credential_add_form(admin_client):
    resp = admin_client.get("/admin/fairy/userbackendcredential/add/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "backend" in body
    assert "token" in body
