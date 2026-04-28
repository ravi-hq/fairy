"""Tests for the backend registry in `session_service/registry.py`.

The registry is the single dispatch point that maps the
`AgentSession.backend` discriminator string onto a concrete `Backend`
implementation. PR 6 introduces it with one entry — `"sprites"` — and
later PRs add Modal and others without touching call sites.
"""

from __future__ import annotations

import pytest

from agent_on_demand.session_service.backends.registry import _build_backends, get_backend
from agent_on_demand.session_service.backends.sprites import SpritesBackend


def test_sprites_is_registered():
    """The default backend must be `"sprites"`. Pin the registered set so
    a future PR that renames or drops the entry trips this test."""
    assert "sprites" in _build_backends()


def test_get_backend_sprites_returns_sprites_backend():
    backend = get_backend("sprites")
    assert isinstance(backend, SpritesBackend)


def test_get_backend_exposes_create_client():
    """Anything in the registry must be callable as a `Backend` — i.e.
    expose `create_client(token)`. Structural check (the `Backend`
    Protocol isn't `@runtime_checkable`)."""
    backend = get_backend("sprites")
    assert callable(getattr(backend, "create_client", None))


def test_get_backend_unknown_raises_keyerror_with_clear_message():
    """An unknown discriminator must raise `KeyError` and the message
    must list the registered backends so operators can see what's
    available."""
    with pytest.raises(KeyError) as exc:
        get_backend("modal")
    msg = str(exc.value)
    assert "modal" in msg
    assert "sprites" in msg


def test_registry_is_singleton_across_calls():
    """`_build_backends()` is `@cache`-d so repeated callers share one
    backend instance — Sprites' websocket monkeypatch only fires once."""
    assert _build_backends() is _build_backends()
    assert get_backend("sprites") is get_backend("sprites")


# ---------- get_client routes through the registry ----------


@pytest.mark.django_db
def test_get_client_routes_through_registry(mocker):
    """`get_client(user, backend=...)` resolves the Backend via
    `get_backend(...)`. Pin so a future refactor that hard-codes
    `SpritesBackend()` is caught."""
    from django.contrib.auth.models import User

    from agent_on_demand.models import UserBackendCredential
    from agent_on_demand.session_service.client import get_client

    user = User.objects.create_user(username="be-sel", password="x")
    cred = UserBackendCredential(user=user, backend="sprites")
    cred.set_token("fake-token")
    cred.save()

    fake_backend = mocker.MagicMock()
    fake_backend.create_client.return_value = mocker.sentinel.client
    mocker.patch(
        "agent_on_demand.session_service.client.get_backend",
        return_value=fake_backend,
    )

    assert get_client(user, backend="sprites") is mocker.sentinel.client
    fake_backend.create_client.assert_called_once_with("fake-token")


def test_get_client_default_backend_is_sprites(mocker):
    """Calling `get_client(user)` without an explicit backend uses
    `"sprites"` — preserves backward compatibility with callers added
    before PR 6."""
    from agent_on_demand.session_service import client as client_module

    user = mocker.MagicMock()
    fake = mocker.MagicMock()
    get_backend_mock = mocker.patch.object(client_module, "get_backend", return_value=fake)
    # PR 8 introduced `_lookup_token` which hits the ORM. Stub it so this
    # test stays focused on the dispatch — the credential paths are
    # exercised in `tests/test_user_backend_credential.py`.
    mocker.patch.object(client_module, "_lookup_token", return_value=None)

    client_module.get_client(user)

    get_backend_mock.assert_called_once_with("sprites")
