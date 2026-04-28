"""Backend registry — maps the `AgentSession.backend` discriminator string
to a concrete `Backend` implementation.

Today there's exactly one entry, ``"sprites"``. Future PRs (the Modal
backend, the Fly Machines backend) plug new backends in here without
touching call sites: provisioning and turn execution route through
``BACKENDS[session.backend]``.

The registry is constructed lazily so import time stays cheap and the
sprites-py websocket monkeypatch in ``SpritesBackend.__init__`` only
fires when something actually asks for the Sprites backend.
"""

from __future__ import annotations

from functools import cache

from .backend import Backend
from .sprites_backend import SpritesBackend


@cache
def _build_backends() -> dict[str, Backend]:
    return {"sprites": SpritesBackend()}


def get_backend(name: str) -> Backend:
    """Resolve a backend by its discriminator string.

    Raises ``KeyError`` with a clear message if the name is not registered —
    the caller's session row has a backend value that no installed backend
    serves.
    """
    backends = _build_backends()
    try:
        return backends[name]
    except KeyError:
        known = ", ".join(sorted(backends)) or "(none)"
        raise KeyError(f"Unknown backend {name!r}; registered backends: {known}") from None
