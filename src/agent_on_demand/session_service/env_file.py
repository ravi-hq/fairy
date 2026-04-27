from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_env_file_body(spec: "SessionSpec", credentials: list[tuple[str, str]]) -> str:
    """Render the body of /tmp/aod-env.

    `credentials` is a list of ``(env_var_name, value)`` pairs, already
    resolved from the ORM by the caller. Each value is ``shlex.quote``-d
    before emission. Precedence: credentials → AOD_SESSION_ID/AOD_MODEL →
    sorted spec.environment.env_vars. Trailing newline is always present.
    """
    lines: list[str] = []
    for env_name, value in credentials:
        lines.append(f"{env_name}={shlex.quote(value)}")
    if spec.runtime_session_id:
        lines.append(f"AOD_SESSION_ID={shlex.quote(spec.runtime_session_id)}")
    if spec.model:
        lines.append(f"AOD_MODEL={shlex.quote(spec.model)}")
    env = spec.environment
    if env is not None:
        for key in sorted(env.env_vars or {}):
            lines.append(f"{key}={shlex.quote(env.env_vars[key])}")
    return "\n".join(lines) + "\n"
