"""Recording fakes for the Sprites SDK, for use in session_service unit tests.

The real SpritesClient / Sprite / filesystem / command objects talk to a remote
service over WebSockets. For unit tests we don't care about the transport; we
care that `provision_session` invoked the right sequence of commands and wrote
the right files. These fakes capture every call and expose lists the tests can
assert against.

After PR 2 of the session-backend extraction, callers in `session_service/`
go through the `BackendClient` / `SessionHandle` Protocols instead of the
sprites SDK directly. To keep existing tests asserting on
`fake_sprites.last_sprite().write_map()` / `command_strings()` working,
`RecordingSpritesClient` and `RecordingSprite` also implement the Protocol
surface — `provision`/`get`/`destroy`/`close` on the client,
`workspace`/`make_command`/`apply_network_policy` on the sprite — and
translate `SpriteError` to `BackendError` so production exception handling
(which expects `BackendError`) catches recorded test failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO

from sprites import SpriteError

from agent_on_demand.session_service.backends import (
    BackendError,
    Command,
    NetworkPolicy,
    SessionHandle,
    WorkspaceFS,
)


@dataclass
class RecordedCommand:
    argv: tuple[str, ...]
    ran: bool = False


@dataclass
class RecordedWrite:
    path: str
    text: str


@dataclass
class _FSPath:
    fs: "_Filesystem"
    path: str

    def __truediv__(self, other: str) -> "_FSPath":
        joined = f"{self.path.rstrip('/')}/{str(other).lstrip('/')}"
        return _FSPath(self.fs, joined)

    def write_text(self, text: str) -> None:
        normalized = "/" + self.path.lstrip("/")
        self.fs._maybe_raise_on_write(normalized)
        self.fs.writes.append(RecordedWrite(path=normalized, text=text))


@dataclass
class _Filesystem:
    writes: list[RecordedWrite] = field(default_factory=list)
    _write_raise_predicates: list[tuple] = field(default_factory=list)

    def __truediv__(self, other: str) -> _FSPath:
        return _FSPath(self, str(other).lstrip("/"))

    def raise_on_write(self, path_predicate, exc: Exception) -> None:
        """Arrange for the next write_text whose normalized path matches
        `path_predicate(path)` to raise `exc`. Predicate can also be a
        substring; matches paths containing it."""
        self._write_raise_predicates.append((path_predicate, exc))

    def _maybe_raise_on_write(self, path: str) -> None:
        for i, (pred, exc) in enumerate(self._write_raise_predicates):
            matched = pred(path) if callable(pred) else pred in path
            if matched:
                del self._write_raise_predicates[i]
                raise exc


class _CommandHandle:
    def __init__(self, recorder: "RecordingSprite", argv: tuple[str, ...]):
        self._recorder = recorder
        self._argv = argv
        # Production callers may assign a writable bytes-like buffer to
        # `cmd.stderr` to capture stderr from the run. _maybe_raise honors it
        # when a predicate provides stderr_text.
        self.stderr: Any = None

    def run(self) -> None:
        recorded = RecordedCommand(argv=self._argv, ran=True)
        self._recorder.commands.append(recorded)
        self._recorder._maybe_raise(self._argv, stderr_buf=self.stderr)


class _BackendCommandAdapter:
    """`Command` Protocol adapter over `_CommandHandle`.

    Translates `SpriteError` from `_CommandHandle.run()` to `BackendError`
    so production code (which catches `BackendError`) can drive the
    recording fake unchanged.
    """

    def __init__(self, handle: _CommandHandle) -> None:
        self._handle = handle

    def set_input(self, data: bytes) -> None:
        # Not asserted on by any current test; recorded form is the argv.
        pass

    def set_output(self, stdout: BinaryIO, stderr: BinaryIO) -> None:
        # The legacy `_CommandHandle` only models stderr capture (the only
        # field the existing `raise_on(..., stderr=...)` predicate writes
        # to). stdout is recorded as argv via `RecordedCommand.argv` in
        # the recorder; passing a real stdout buffer here is harmless but
        # nothing populates it. Production always passes `io.BytesIO()`.
        self._handle.stderr = stderr

    def run(self) -> int:
        try:
            self._handle.run()
        except SpriteError as e:
            raise BackendError(str(e)) from e
        return 0


class _BackendWorkspaceAdapter:
    """`WorkspaceFS` Protocol adapter over `_Filesystem`.

    `write_text(path, content)` is translated into the existing
    `(fs / path).write_text(content)` call so `raise_on_write` predicates
    fire the same way they did in PR 1.
    """

    def __init__(self, fs: _Filesystem) -> None:
        self._fs = fs

    def write_text(self, path: str, content: str) -> None:
        try:
            (self._fs / path.lstrip("/")).write_text(content)
        except SpriteError as e:
            raise BackendError(str(e)) from e

    def chmod(self, path: str, mode: int) -> None:
        # No-op — current tests don't observe chmod calls; the real
        # provisioning script does its chmods inside the bash script.
        pass


class RecordingSprite:
    """Drop-in replacement for sprites.Sprite in unit tests.

    Implements both the legacy sprites SDK shape (`filesystem()`,
    `command()`, `update_network_policy()`) and the `SessionHandle`
    Protocol shape (`workspace()`, `make_command()`,
    `apply_network_policy()`). The two surfaces share underlying
    recording state so tests can assert on `writes` / `commands` /
    `policies` regardless of which surface drove the call.
    """

    def __init__(self, name: str):
        self.name = name
        self._fs = _Filesystem()
        self.commands: list[RecordedCommand] = []
        self.policies: list[Any] = []
        self.interrupt_calls: int = 0
        self._raise_on_predicates: list[tuple] = []

    # Legacy sprites SDK shape — runtimes still call these in PR 2;
    # PR 3 will switch them to the SessionHandle methods below.

    def filesystem(self) -> _Filesystem:
        return self._fs

    def command(self, *argv: str, timeout: float | None = None) -> _CommandHandle:
        return _CommandHandle(self, tuple(argv))

    def update_network_policy(self, policy: Any) -> None:
        self.policies.append(policy)
        self._maybe_raise(("update_network_policy",))

    # SessionHandle Protocol shape.

    def workspace(self) -> WorkspaceFS:
        return _BackendWorkspaceAdapter(self._fs)

    def make_command(
        self,
        *args: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Command:
        return _BackendCommandAdapter(_CommandHandle(self, tuple(args)))

    def apply_network_policy(self, policy: NetworkPolicy) -> None:
        try:
            self.update_network_policy(policy)
        except SpriteError as e:
            raise BackendError(str(e)) from e

    def interrupt_running_commands(self) -> None:
        self._maybe_raise(("interrupt_running_commands",))
        self.interrupt_calls += 1

    @property
    def writes(self) -> list[RecordedWrite]:
        return self._fs.writes

    def write_map(self) -> dict[str, str]:
        """Path → text for the most recent write at each path."""
        out: dict[str, str] = {}
        for w in self.writes:
            out[w.path] = w.text
        return out

    def command_strings(self) -> list[str]:
        """Flat list of 'arg1 arg2 ...' for each recorded command (nicer to grep)."""
        return [" ".join(c.argv) for c in self.commands]

    def shell_strings(self) -> list[str]:
        """Every grep-able shell line: each recorded command, plus each
        non-comment line of any `.sh` scripts written to the filesystem.

        Use this when asserting that a shell operation happened — it doesn't
        matter whether the real invocation was a direct `sprite.command()`
        or a line inside a bulk provisioning script. Use `command_strings()`
        when asserting the *count* of sprite.command round trips."""
        out: list[str] = [" ".join(c.argv) for c in self.commands]
        for w in self.writes:
            if w.path.endswith(".sh"):
                for line in w.text.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        out.append(stripped)
        return out

    def raise_on(self, predicate, exc: Exception, *, stderr: bytes = b"") -> None:
        """Arrange for the next command matching `predicate(argv_tuple)` to
        raise `exc` instead of succeeding. Predicate can also be the string
        'update_network_policy' to target the policy call.

        When `stderr` is non-empty and the matched call is a
        `_CommandHandle` whose caller assigned a writable buffer to
        `cmd.stderr`, those bytes are written to that buffer just before
        the exception is raised — mirroring the real Sprites SDK, which
        flushes captured stderr to the buffer before surfacing
        SpriteError."""
        self._raise_on_predicates.append((predicate, exc, stderr))

    def _maybe_raise(self, argv: tuple[str, ...], stderr_buf: Any = None) -> None:
        for i, (pred, exc, stderr) in enumerate(self._raise_on_predicates):
            matched = pred(argv) if callable(pred) else (bool(argv) and argv[0] == pred)
            if matched:
                del self._raise_on_predicates[i]
                if stderr and stderr_buf is not None:
                    stderr_buf.write(stderr)
                raise exc


class RecordingSpritesClient:
    """Drop-in replacement for sprites.SpritesClient.

    Implements both the legacy sprites SDK shape (`create_sprite`,
    `get_sprite`, `delete_sprite`) and the `BackendClient` Protocol shape
    (`provision`, `get`, `destroy`, `close`). Protocol methods translate
    `SpriteError` to `BackendError` to match production exception handling.
    """

    def __init__(self):
        self.sprites: dict[str, RecordingSprite] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._create_error: Exception | None = None

    # Legacy sprites SDK shape — used by tests that arrange test doubles via
    # `mocker.patch.object(fake_sprites, "delete_sprite", ...)`.

    def create_sprite(self, name: str) -> RecordingSprite:
        self.created.append(name)
        if self._create_error is not None:
            err = self._create_error
            self._create_error = None
            raise err
        sprite = RecordingSprite(name)
        self.sprites[name] = sprite
        return sprite

    def get_sprite(self, name: str) -> RecordingSprite:
        if name not in self.sprites:
            self.sprites[name] = RecordingSprite(name)
        return self.sprites[name]

    def delete_sprite(self, name: str) -> None:
        self.deleted.append(name)

    # BackendClient Protocol shape.

    def provision(self, name: str) -> SessionHandle:
        try:
            return self.create_sprite(name)
        except SpriteError as e:
            raise BackendError(str(e)) from e

    def get(self, name: str) -> SessionHandle:
        try:
            return self.get_sprite(name)
        except SpriteError as e:
            raise BackendError(str(e)) from e

    def destroy(self, name: str) -> None:
        try:
            self.delete_sprite(name)
        except SpriteError as e:
            raise BackendError(str(e)) from e

    def close(self) -> None:
        pass

    def raise_on_create(self, exc: Exception) -> None:
        self._create_error = exc

    def last_sprite(self) -> RecordingSprite:
        return self.sprites[self.created[-1]]
