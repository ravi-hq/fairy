"""Session execution orchestration over a swappable backend.

Views, signals, and other callers should go through this package instead
of touching backend SDKs directly. The package layout:

- ``backends/`` — the `Backend` Protocol and concrete adapters (Sprites
  today; Modal/Fly Machines in future PRs). All coupling to backend SDKs
  is confined here.
- ``provisioning/`` — the orchestrator (`provision_session`, `resume_session`,
  `destroy_session`), per-stage helpers, and the pure script-building
  functions that compose the bash payload run on the backend.
- ``client.py`` — credential lookup + `Backend.create_client` glue.
- ``specs.py`` / ``spec_factory.py`` — backend-neutral session spec types
  and the ORM → spec hydration path.
- ``turn.py`` / ``tasks.py`` / ``turn_argv.py`` / ``turn_outcome.py`` —
  per-turn execution: enqueue, Procrastinate task body, argv builder,
  and outcome resolution.
"""

from .client import get_client
from .errors import (
    NoBackendCredentialsError,
    NoSpritesKeyError,
    ProvisionError,
    SessionHandleNotFound,
    SessionServiceError,
)
from .provisioning import destroy_session, provision_session, resume_session
from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec
from .tasks import destroy_session_task, provision_session_task
from .turn import run_turn

__all__ = [
    "McpServerSpec",
    "NoBackendCredentialsError",
    "NoSpritesKeyError",
    "ProvisionError",
    "RepoSpec",
    "SessionHandleNotFound",
    "SessionServiceError",
    "SessionSpec",
    "SkillSpec",
    "destroy_session",
    "destroy_session_task",
    "get_client",
    "provision_session",
    "provision_session_task",
    "resume_session",
    "run_turn",
]
