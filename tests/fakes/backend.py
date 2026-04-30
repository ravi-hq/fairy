"""Recording fakes for the Backend Protocol, for use in unit tests.

Mirrors the recording style of `tests/fakes/sprite.py` but conforms to
the Protocol in `agent_on_demand.session_service.backends`. Tests assert
on `.writes`, `.commands`, and `.network_policies` lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Callable

from agent_on_demand.session_service.backends import (
    BackendClient,
    Command,
    NetworkPolicy,
    SessionHandle,
    SessionNotFoundError,
    WorkspaceFS,
)


@dataclass
class RecordedWrite:
    path: str
    content: str


@dataclass
class RecordedChmod:
    path: str
    mode: int


@dataclass
class RecordedCommand:
    argv: tuple[str, ...]
    cwd: str | None = None
    timeout: float | None = None
    ran: bool = False
    exit_code: int = 0
    stdin: bytes | None = None


class RecordingFS:
    def __init__(self) -> None:
        self.writes: list[RecordedWrite] = []
        self.chmods: list[RecordedChmod] = []
        self._write_raise_predicates: list[tuple[Any, Exception]] = []

    def write_text(self, path: str, content: str) -> None:
        normalized = "/" + path.lstrip("/")
        for i, (pred, exc) in enumerate(self._write_raise_predicates):
            matched = pred(normalized) if callable(pred) else pred in normalized
            if matched:
                del self._write_raise_predicates[i]
                raise exc
        self.writes.append(RecordedWrite(path=normalized, content=content))

    def chmod(self, path: str, mode: int) -> None:
        self.chmods.append(RecordedChmod(path="/" + path.lstrip("/"), mode=mode))

    def raise_on_write(self, predicate: Any, exc: Exception) -> None:
        """Arrange the next matching write_text to raise.

        ``predicate`` is matched against the normalized absolute path. A
        callable is invoked with that path and must return truthy; any
        other value is treated as a **substring** check (``predicate in
        path``). Single-shot — the predicate is removed after firing."""
        self._write_raise_predicates.append((predicate, exc))


class RecordingCommand:
    def __init__(
        self,
        handle: "RecordingHandle",
        argv: tuple[str, ...],
        cwd: str | None,
        timeout: float | None,
    ) -> None:
        self._handle = handle
        self._record = RecordedCommand(argv=argv, cwd=cwd, timeout=timeout)
        self._stdout: BinaryIO | None = None
        self._stderr: BinaryIO | None = None
        handle.commands.append(self._record)

    def set_input(self, data: bytes) -> None:
        self._record.stdin = data

    def set_output(self, stdout: BinaryIO, stderr: BinaryIO) -> None:
        self._stdout = stdout
        self._stderr = stderr

    def run(self) -> int:
        self._record.ran = True
        for i, (pred, action) in enumerate(self._handle._command_actions):
            matched = (
                pred(self._record.argv)
                if callable(pred)
                else (bool(self._record.argv) and self._record.argv[0] == pred)
            )
            if matched:
                del self._handle._command_actions[i]
                exit_code, stderr_bytes, exc = action
                if stderr_bytes and self._stderr is not None:
                    self._stderr.write(stderr_bytes)
                if exc is not None:
                    raise exc
                self._record.exit_code = exit_code
                return exit_code
        return 0


class RecordingHandle:
    def __init__(self, name: str) -> None:
        self._name = name
        self._fs = RecordingFS()
        self.commands: list[RecordedCommand] = []
        self.network_policies: list[NetworkPolicy] = []
        self.interrupt_calls: int = 0
        self._command_actions: list[tuple[Any, tuple[int, bytes, Exception | None]]] = []
        self._policy_raise: Exception | None = None
        self._interrupt_raise: Exception | None = None

    @property
    def name(self) -> str:
        return self._name

    def workspace(self) -> WorkspaceFS:
        return self._fs

    def make_command(
        self,
        *args: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Command:
        return RecordingCommand(self, tuple(args), cwd, timeout)

    def apply_network_policy(self, policy: NetworkPolicy) -> None:
        if self._policy_raise is not None:
            exc = self._policy_raise
            self._policy_raise = None
            raise exc
        self.network_policies.append(policy)

    def interrupt_running_commands(self) -> None:
        if self._interrupt_raise is not None:
            exc = self._interrupt_raise
            self._interrupt_raise = None
            raise exc
        self.interrupt_calls += 1

    @property
    def writes(self) -> list[RecordedWrite]:
        return self._fs.writes

    @property
    def chmods(self) -> list[RecordedChmod]:
        return self._fs.chmods

    def raise_on_write(self, predicate: Any, exc: Exception) -> None:
        self._fs.raise_on_write(predicate, exc)

    def set_command_outcome(
        self,
        predicate: Callable[[tuple[str, ...]], bool] | str,
        *,
        exit_code: int = 0,
        stderr: bytes = b"",
        exc: Exception | None = None,
    ) -> None:
        """Arrange the next matching command to exit with ``exit_code``,
        write ``stderr`` to the assigned buffer, or raise ``exc``.

        ``predicate`` is matched against the recorded argv tuple. A
        callable is invoked with the full argv and must return truthy;
        a string is treated as an **exact match against argv[0]** (not a
        substring of the full argv). Single-shot. This deliberately
        differs from :meth:`raise_on_write` — write paths are matched by
        substring while command argv is matched by program name."""
        self._command_actions.append((predicate, (exit_code, stderr, exc)))

    def raise_on_apply_network_policy(self, exc: Exception) -> None:
        self._policy_raise = exc

    def raise_on_interrupt(self, exc: Exception) -> None:
        self._interrupt_raise = exc


class RecordingBackendClient:
    def __init__(self) -> None:
        self.handles: dict[str, RecordingHandle] = {}
        self.created: list[str] = []
        self.deleted: list[str] = []
        self.closed = False
        self._provision_error: Exception | None = None
        self._get_missing: set[str] = set()

    def provision(self, name: str) -> SessionHandle:
        self.created.append(name)
        if self._provision_error is not None:
            err = self._provision_error
            self._provision_error = None
            raise err
        handle = RecordingHandle(name)
        self.handles[name] = handle
        return handle

    def get(self, name: str) -> SessionHandle:
        if name in self._get_missing:
            raise SessionNotFoundError(f"sprite {name} not found")
        if name not in self.handles:
            self.handles[name] = RecordingHandle(name)
        return self.handles[name]

    def destroy(self, name: str) -> None:
        self.deleted.append(name)

    def close(self) -> None:
        self.closed = True

    def raise_on_provision(self, exc: Exception) -> None:
        self._provision_error = exc

    def mark_missing(self, name: str) -> None:
        self._get_missing.add(name)

    def last_handle(self) -> RecordingHandle:
        return self.handles[self.created[-1]]


class FakeBackend:
    """Recording `Backend` impl. `create_client` returns a fresh
    `RecordingBackendClient` per call unless one was preset."""

    def __init__(self, client: RecordingBackendClient | None = None) -> None:
        self._client = client

    def create_client(self, token: str) -> BackendClient:
        if self._client is not None:
            return self._client
        return RecordingBackendClient()


__all__ = [
    "FakeBackend",
    "RecordingBackendClient",
    "RecordingCommand",
    "RecordingFS",
    "RecordingHandle",
    "RecordedChmod",
    "RecordedCommand",
    "RecordedWrite",
]
