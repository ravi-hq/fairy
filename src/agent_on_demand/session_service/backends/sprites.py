"""Sprites adapter for the Backend Protocol.

This is the only module that imports `sprites` directly. It wraps
`SpritesClient` / `Sprite` / `SpriteFilesystem` / `Cmd` to the
backend-neutral Protocol in `backend.py` and translates exceptions:

- `sprites.NotFoundError` → `SessionNotFoundError`
- other `sprites.SpriteError` → `BackendError`
- `sprites.ExecError` from `cmd.run()` → exit-code int (NOT raised)
- transport failures (anything else) → `BackendError`

The websocket close-timeout monkeypatch lives in `__init__` and is
idempotent — repeated `SpritesBackend()` instantiations don't re-patch.
"""

from __future__ import annotations

import io
import logging
from typing import Any, BinaryIO

import sprites
import sprites.session as _sprites_session
from django.conf import settings

from .base import (
    BackendClient,
    BackendError,
    Command,
    NetworkPolicy,
    SessionHandle,
    SessionNotFoundError,
    WorkspaceFS,
)

logger = logging.getLogger(__name__)

# Re-exported for callers that still need to catch sprites-native
# exceptions during the PR 2 → PR 4 transition. `tasks.py` catches
# `ExecError` from the threading core (PR 4 scope); `provisioning_stages.py`
# catches `SpriteError` from `runtime.install` / `runtime.write_config`
# until those runtimes port to the Protocol (PR 3). Removed once those
# call paths are on the Protocol.
ExecError = sprites.ExecError
SpriteError = sprites.SpriteError


_PATCH_MARKER = "_aod_close_timeout_patched"


def _apply_websocket_patch() -> None:
    """Lower sprites-py's websocket close_timeout to 0.5s.

    The default 10s makes every `cmd.run()` block for the full timeout
    on shutdown. Patching `websockets.connect` to default `close_timeout`
    yields a ~22x speedup. Idempotent — flagged on the module so repeated
    `SpritesBackend()` instantiations don't re-wrap.
    """
    import sprites.websocket as _ws

    if getattr(_ws.websockets, _PATCH_MARKER, False):
        return
    _orig_connect = _ws.websockets.connect

    def _patched_connect(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("close_timeout", 0.5)
        return _orig_connect(*args, **kwargs)

    _ws.websockets.connect = _patched_connect  # type: ignore[misc,assignment]
    setattr(_ws.websockets, _PATCH_MARKER, True)


class _SpritesWorkspaceFS:
    def __init__(self, fs: sprites.SpriteFilesystem):
        self._fs = fs

    def write_text(self, path: str, content: str) -> None:
        try:
            (self._fs / path.lstrip("/")).write_text(content)
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e

    def chmod(self, path: str, mode: int) -> None:
        try:
            (self._fs / path.lstrip("/")).chmod(mode)
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e


class _SpritesCommand:
    def __init__(self, cmd: Any):
        self._cmd = cmd

    def set_input(self, data: bytes) -> None:
        self._cmd.stdin = io.BytesIO(data)

    def set_output(self, stdout: BinaryIO, stderr: BinaryIO) -> None:
        self._cmd.stdout = stdout
        self._cmd.stderr = stderr

    def run(self) -> int:
        try:
            self._cmd.run()
        except sprites.ExecError as e:
            return e.exit_code()
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e
        return 0


class _SpritesHandle:
    def __init__(self, sprite: sprites.Sprite):
        self._sprite = sprite

    @property
    def name(self) -> str:
        return self._sprite.name

    def workspace(self) -> WorkspaceFS:
        return _SpritesWorkspaceFS(self._sprite.filesystem())

    def make_command(
        self,
        *args: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Command:
        return _SpritesCommand(self._sprite.command(*args, cwd=cwd, timeout=timeout))

    def apply_network_policy(self, policy: NetworkPolicy) -> None:
        translated = sprites.NetworkPolicy(
            rules=[sprites.PolicyRule(domain=r.domain, action=r.action) for r in policy.rules],
        )
        try:
            self._sprite.update_network_policy(translated)
        except sprites.NotFoundError as e:
            raise SessionNotFoundError(str(e)) from e
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e

    def interrupt_running_commands(self) -> None:
        """Send SIGTERM to every active sprites-side exec session on this
        sprite. Each ``Cmd.run()`` we spawn registers a server-side
        session; killing it makes the in-Sprite agent process exit, the
        SDK call returns with ``ExecError``, and the worker thread
        finalizes the turn. Best-effort: per-session NotFoundError is
        swallowed (race with natural completion); transport-layer
        failures surface as ``BackendError``."""
        try:
            sessions = _sprites_session.list_sessions(self._sprite)
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e
        for s in sessions:
            if not s.is_active:
                continue
            try:
                _sprites_session.kill_session(self._sprite, s.id, signal="SIGTERM", timeout=5)
            except sprites.NotFoundError:
                pass
            except sprites.SpriteError as e:
                logger.warning("kill_session failed for %s on %s: %s", s.id, self._sprite.name, e)


class _SpritesBackendClient:
    def __init__(self, client: sprites.SpritesClient):
        self._client = client

    def provision(self, name: str) -> SessionHandle:
        # NotFoundError from create_sprite means a referenced pool/template
        # is missing — the session itself doesn't exist yet, so we don't
        # surface SessionNotFoundError here. Callers see this as a generic
        # BackendError, which is what they are already prepared to handle
        # for any other create failure.
        try:
            return _SpritesHandle(self._client.create_sprite(name))
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e

    def get(self, name: str) -> SessionHandle:
        try:
            return _SpritesHandle(self._client.get_sprite(name))
        except sprites.NotFoundError as e:
            raise SessionNotFoundError(str(e)) from e
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e

    def destroy(self, name: str) -> None:
        try:
            self._client.delete_sprite(name)
        except sprites.NotFoundError as e:
            raise SessionNotFoundError(str(e)) from e
        except sprites.SpriteError as e:
            raise BackendError(str(e)) from e

    def close(self) -> None:
        self._client.close()


class SpritesBackend:
    """`Backend` implementation for sprites.dev.

    Constructing the backend applies the websocket close-timeout patch.
    Patch is idempotent — multiple instances do not re-wrap.
    """

    def __init__(self) -> None:
        _apply_websocket_patch()

    def create_client(self, token: str) -> BackendClient:
        return _SpritesBackendClient(
            sprites.SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)
        )
