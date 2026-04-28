"""Backend abstraction: the Protocol, the registry, and concrete adapters.

`base` defines the Protocol shape (`Backend`, `BackendClient`,
`SessionHandle`, errors). `sprites` is the only adapter today.
`registry` resolves the `AgentSession.backend` discriminator string to a
concrete backend.
"""

from .base import (
    Backend,
    BackendClient,
    BackendError,
    Command,
    ExecutionError,
    NetworkPolicy,
    PolicyRule,
    SessionHandle,
    SessionNotFoundError,
    WorkspaceFS,
)
from .registry import get_backend
from .sprites import ExecError, SpriteError, SpritesBackend

__all__ = [
    "Backend",
    "BackendClient",
    "BackendError",
    "Command",
    "ExecError",
    "ExecutionError",
    "NetworkPolicy",
    "PolicyRule",
    "SessionHandle",
    "SessionNotFoundError",
    "SpriteError",
    "SpritesBackend",
    "WorkspaceFS",
    "get_backend",
]
