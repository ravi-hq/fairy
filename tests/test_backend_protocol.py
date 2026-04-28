"""Tests for the SpritesBackend adapter — exception translation, command
exit-code semantics, filesystem op forwarding, and network-policy translation."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest
import sprites

from agent_on_demand.session_service.backends.base import (
    BackendError,
    NetworkPolicy,
    PolicyRule,
    SessionNotFoundError,
)
from agent_on_demand.session_service.backends.sprites import (
    SpritesBackend,
    _SpritesBackendClient,
    _SpritesCommand,
    _SpritesHandle,
)


def _fake_sprites_client():
    """A MagicMock standing in for sprites.SpritesClient. Tests configure
    `.create_sprite`, `.get_sprite`, `.delete_sprite`, `.close` directly."""
    return MagicMock(spec=sprites.SpritesClient)


def _fake_sprite(name: str = "test"):
    """A MagicMock standing in for sprites.Sprite."""
    sprite = MagicMock(spec=sprites.Sprite)
    sprite.name = name
    return sprite


# ---------- _SpritesBackendClient ----------


def test_provision_does_not_translate_not_found_to_session_not_found():
    """NotFoundError from create_sprite means a referenced pool/template
    is missing, not the session — so it surfaces as a generic
    BackendError, NOT SessionNotFoundError."""
    client_mock = _fake_sprites_client()
    client_mock.create_sprite.side_effect = sprites.NotFoundError("pool missing")
    bc = _SpritesBackendClient(client_mock)
    with pytest.raises(BackendError) as exc:
        bc.provision("name")
    assert not isinstance(exc.value, SessionNotFoundError)


def test_provision_translates_other_sprite_errors_to_backend_error():
    client_mock = _fake_sprites_client()
    client_mock.create_sprite.side_effect = sprites.SpriteError("boom")
    bc = _SpritesBackendClient(client_mock)
    with pytest.raises(BackendError) as exc:
        bc.provision("name")
    # Not a SessionNotFoundError specifically.
    assert not isinstance(exc.value, SessionNotFoundError)


def test_provision_returns_handle_with_name():
    sprite = _fake_sprite("abc")
    client_mock = _fake_sprites_client()
    client_mock.create_sprite.return_value = sprite
    handle = _SpritesBackendClient(client_mock).provision("abc")
    assert handle.name == "abc"


def test_get_translates_not_found_error():
    client_mock = _fake_sprites_client()
    client_mock.get_sprite.side_effect = sprites.NotFoundError("missing")
    bc = _SpritesBackendClient(client_mock)
    with pytest.raises(SessionNotFoundError):
        bc.get("name")


def test_destroy_translates_other_sprite_errors_to_backend_error():
    client_mock = _fake_sprites_client()
    client_mock.delete_sprite.side_effect = sprites.SpriteError("boom")
    bc = _SpritesBackendClient(client_mock)
    with pytest.raises(BackendError):
        bc.destroy("name")


def test_close_forwards():
    client_mock = _fake_sprites_client()
    _SpritesBackendClient(client_mock).close()
    client_mock.close.assert_called_once()


# ---------- _SpritesCommand ----------


def test_command_run_returns_zero_on_clean_exit():
    cmd_mock = MagicMock()
    cmd_mock.run.return_value = None
    assert _SpritesCommand(cmd_mock).run() == 0


def test_command_run_returns_exit_code_on_exec_error():
    cmd_mock = MagicMock()
    cmd_mock.run.side_effect = sprites.ExecError("nonzero", 137, b"", b"")
    # ExecError.exit_code() is a method (not a property) in sprites-py;
    # the adapter must call it.
    assert _SpritesCommand(cmd_mock).run() == 137


def test_command_run_raises_backend_error_on_other_sprite_errors():
    cmd_mock = MagicMock()
    cmd_mock.run.side_effect = sprites.SpriteError("transport failure")
    with pytest.raises(BackendError):
        _SpritesCommand(cmd_mock).run()


def test_command_set_input_assigns_bytesio_to_stdin():
    cmd_mock = MagicMock()
    _SpritesCommand(cmd_mock).set_input(b"hello")
    assert isinstance(cmd_mock.stdin, io.BytesIO)
    cmd_mock.stdin.seek(0)
    assert cmd_mock.stdin.read() == b"hello"


def test_command_set_output_assigns_writers():
    cmd_mock = MagicMock()
    out = io.BytesIO()
    err = io.BytesIO()
    _SpritesCommand(cmd_mock).set_output(out, err)
    assert cmd_mock.stdout is out
    assert cmd_mock.stderr is err


# ---------- _SpritesHandle / WorkspaceFS ----------


def test_workspace_write_text_reaches_spritepath():
    sprite = _fake_sprite()
    fs_mock = MagicMock()
    path_mock = MagicMock()
    sprite.filesystem.return_value = fs_mock
    fs_mock.__truediv__.return_value = path_mock

    _SpritesHandle(sprite).workspace().write_text("/etc/foo", "body")
    fs_mock.__truediv__.assert_called_once_with("etc/foo")
    path_mock.write_text.assert_called_once_with("body")


def test_workspace_chmod_reaches_spritepath():
    sprite = _fake_sprite()
    fs_mock = MagicMock()
    path_mock = MagicMock()
    sprite.filesystem.return_value = fs_mock
    fs_mock.__truediv__.return_value = path_mock

    _SpritesHandle(sprite).workspace().chmod("/run.sh", 0o755)
    fs_mock.__truediv__.assert_called_once_with("run.sh")
    path_mock.chmod.assert_called_once_with(0o755)


def test_workspace_write_text_translates_sprite_error():
    sprite = _fake_sprite()
    fs_mock = MagicMock()
    path_mock = MagicMock()
    sprite.filesystem.return_value = fs_mock
    fs_mock.__truediv__.return_value = path_mock
    path_mock.write_text.side_effect = sprites.SpriteError("disk full")

    with pytest.raises(BackendError):
        _SpritesHandle(sprite).workspace().write_text("/x", "y")


# ---------- _SpritesHandle.make_command ----------


def test_make_command_passes_args_cwd_timeout():
    sprite = _fake_sprite()
    cmd_mock = MagicMock()
    sprite.command.return_value = cmd_mock

    handle = _SpritesHandle(sprite)
    cmd = handle.make_command("bash", "-lc", "true", cwd="/home/sprite", timeout=30.0)

    sprite.command.assert_called_once_with("bash", "-lc", "true", cwd="/home/sprite", timeout=30.0)
    assert isinstance(cmd, _SpritesCommand)


# ---------- _SpritesHandle.apply_network_policy ----------


def test_apply_network_policy_translates_to_sprites_types():
    sprite = _fake_sprite()
    policy = NetworkPolicy(
        rules=(
            PolicyRule(domain="github.com", action="allow"),
            PolicyRule(domain="*", action="deny"),
        )
    )
    _SpritesHandle(sprite).apply_network_policy(policy)

    assert sprite.update_network_policy.call_count == 1
    translated = sprite.update_network_policy.call_args[0][0]
    assert isinstance(translated, sprites.NetworkPolicy)
    assert len(translated.rules) == 2
    assert translated.rules[0].domain == "github.com"
    assert translated.rules[0].action == "allow"
    assert translated.rules[1].domain == "*"
    assert translated.rules[1].action == "deny"


def test_apply_network_policy_translates_not_found_error():
    sprite = _fake_sprite()
    sprite.update_network_policy.side_effect = sprites.NotFoundError("missing")
    with pytest.raises(SessionNotFoundError):
        _SpritesHandle(sprite).apply_network_policy(NetworkPolicy())


def test_apply_network_policy_translates_other_sprite_errors():
    sprite = _fake_sprite()
    sprite.update_network_policy.side_effect = sprites.SpriteError("boom")
    with pytest.raises(BackendError):
        _SpritesHandle(sprite).apply_network_policy(NetworkPolicy())


# ---------- SpritesBackend ----------


def test_sprites_backend_create_client_uses_settings_base_url(mocker, settings):
    settings.SPRITES_BASE_URL = "https://api.example.test"
    constructor = mocker.patch(
        "agent_on_demand.session_service.backends.sprites.sprites.SpritesClient"
    )
    SpritesBackend().create_client("token-abc")
    constructor.assert_called_once_with(token="token-abc", base_url="https://api.example.test")


def test_sprites_backend_websocket_patch_is_idempotent(mocker):
    """Multiple SpritesBackend() instantiations should not re-wrap
    websockets.connect."""
    import sprites.websocket as ws

    # Reset the marker so we can observe the first patch happen.
    if hasattr(ws.websockets, "_aod_close_timeout_patched"):
        delattr(ws.websockets, "_aod_close_timeout_patched")
    original = ws.websockets.connect

    SpritesBackend()
    after_first = ws.websockets.connect
    assert after_first is not original

    SpritesBackend()
    after_second = ws.websockets.connect
    assert after_second is after_first  # no re-wrap
