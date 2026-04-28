"""Backend-neutral session spec types and the ORM-to-spec hydration path.

`types` carries the frozen dataclasses (`SessionSpec`, `RepoSpec`,
`McpServerSpec`, `SkillSpec`) that provisioning, runtimes, and turn
execution consume. `factory` carries `build_spec_for_session`, the single
path that hydrates persisted session state into a `SessionSpec`.
"""

from .factory import build_spec_for_session
from .types import McpServerSpec, RepoSpec, SessionSpec, SkillSpec

__all__ = [
    "McpServerSpec",
    "RepoSpec",
    "SessionSpec",
    "SkillSpec",
    "build_spec_for_session",
]
