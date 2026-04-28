"""Backend Protocol for session execution hosts.

Sprites is one implementation; future hosts (e.g. Modal sandboxes) will
plug in by implementing this Protocol. All `session_service/` callers go
through these types — direct `from sprites import` is confined to
`sprites_backend.py`.

The shape was designed in
`thoughts/research/2026-04-18-session-backend-abstraction.md` and refined
in `thoughts/plans/2026-04-27-session-backend-extraction.md`:

- `Command.run()` returns the exit code directly. Non-zero exits are NOT
  raised — `ExecutionError` is reserved for transport-layer failures
  where the command never produced an exit code.
- `WorkspaceFS.chmod` is a filesystem operation, not a `chmod` shell-out.
- `set_executable` is omitted from the Protocol; callers use `chmod`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Protocol


class BackendError(Exception):
    """Base for all backend-layer failures."""


class SessionNotFoundError(BackendError):
    """The backend has no session with the requested name."""


class ExecutionError(BackendError):
    """A command could not be executed at all (transport failure).

    Non-zero exits are NOT raised — they're returned as the int from
    `Command.run()`. This exception covers the case where the SDK could
    not start, monitor, or report the command's exit status.
    """

    def __init__(
        self,
        message: str,
        exit_code: int = -1,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ):
        super().__init__(message)
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class PolicyRule:
    domain: str
    action: str  # "allow" | "deny"


@dataclass(frozen=True)
class NetworkPolicy:
    rules: tuple[PolicyRule, ...] = ()


class WorkspaceFS(Protocol):
    """Per-handle filesystem operations."""

    def write_text(self, path: str, content: str) -> None: ...
    def chmod(self, path: str, mode: int) -> None: ...


class Command(Protocol):
    """A command staged for execution on a session handle.

    Configuration via `set_input` / `set_output` happens between
    construction and `run()`. `run()` blocks until the command exits and
    returns the exit code; it does NOT raise on non-zero exits.
    """

    def set_input(self, data: bytes) -> None: ...
    def set_output(self, stdout: BinaryIO, stderr: BinaryIO) -> None: ...
    def run(self) -> int: ...


class SessionHandle(Protocol):
    """A live session on the backend."""

    @property
    def name(self) -> str: ...
    def workspace(self) -> WorkspaceFS: ...
    def make_command(
        self,
        *args: str,
        cwd: str | None = None,
        timeout: float | None = None,
    ) -> Command: ...
    def apply_network_policy(self, policy: NetworkPolicy) -> None: ...


class BackendClient(Protocol):
    """An auth-scoped client for one user's sessions on the backend."""

    def provision(self, name: str) -> SessionHandle: ...
    def get(self, name: str) -> SessionHandle: ...
    def destroy(self, name: str) -> None: ...
    def close(self) -> None: ...


class Backend(Protocol):
    """A backend implementation. Stateless — `create_client` returns
    per-user clients."""

    def create_client(self, token: str) -> BackendClient: ...
