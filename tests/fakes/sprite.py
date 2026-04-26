"""Recording fakes for the Sprites SDK, for use in session_service unit tests.

The real SpritesClient / Sprite / filesystem / command objects talk to a remote
service over WebSockets. For unit tests we don't care about the transport; we
care that `provision_session` invoked the right sequence of commands and wrote
the right files. These fakes capture every call and expose lists the tests can
assert against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

    def run(self) -> None:
        recorded = RecordedCommand(argv=self._argv, ran=True)
        self._recorder.commands.append(recorded)
        self._recorder._maybe_raise(self._argv)


class RecordingSprite:
    """Drop-in replacement for sprites.Sprite in unit tests."""

    def __init__(self, name: str):
        self.name = name
        self._fs = _Filesystem()
        self.commands: list[RecordedCommand] = []
        self.policies: list[Any] = []
        self._raise_on_predicates: list[tuple] = []

    def filesystem(self) -> _Filesystem:
        return self._fs

    def command(self, *argv: str, timeout: float | None = None) -> _CommandHandle:
        return _CommandHandle(self, tuple(argv))

    def update_network_policy(self, policy: Any) -> None:
        self.policies.append(policy)
        self._maybe_raise(("update_network_policy",))

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

    def raise_on(self, predicate, exc: Exception) -> None:
        """Arrange for the next command matching `predicate(argv_tuple)` to
        raise `exc` instead of succeeding. Predicate can also be the string
        'update_network_policy' to target the policy call."""
        self._raise_on_predicates.append((predicate, exc))

    def _maybe_raise(self, argv: tuple[str, ...]) -> None:
        for i, (pred, exc) in enumerate(self._raise_on_predicates):
            if callable(pred):
                if pred(argv):
                    del self._raise_on_predicates[i]
                    raise exc
            else:
                if argv and argv[0] == pred:
                    del self._raise_on_predicates[i]
                    raise exc


class RecordingSpritesClient:
    """Drop-in replacement for sprites.SpritesClient."""

    def __init__(self):
        self.sprites: dict[str, RecordingSprite] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self._create_error: Exception | None = None

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

    def raise_on_create(self, exc: Exception) -> None:
        self._create_error = exc

    def last_sprite(self) -> RecordingSprite:
        return self.sprites[self.created[-1]]
