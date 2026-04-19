"""Owns the Sprites client lifecycle and session orchestration.

Views, signals, and other callers should go through this package instead of
calling `sprites.*` primitives directly. All coupling to Sprites lives here,
so it can later be placed behind a Protocol without touching the call sites.
"""

from .client import get_client
from .errors import (
    NoSpritesKeyError,
    ProvisionError,
    SessionHandleNotFound,
    SessionServiceError,
)
from .provisioning import destroy_session, provision_session, resume_session
from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec
from .tasks import provision_session_task
from .turn import run_turn

__all__ = [
    "McpServerSpec",
    "NoSpritesKeyError",
    "ProvisionError",
    "RepoSpec",
    "SessionHandleNotFound",
    "SessionServiceError",
    "SessionSpec",
    "SkillSpec",
    "destroy_session",
    "get_client",
    "provision_session",
    "provision_session_task",
    "resume_session",
    "run_turn",
]
