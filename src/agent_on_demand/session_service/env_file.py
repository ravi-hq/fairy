"""Compose the body of `/tmp/aod-env`.

The file is sourced (with `set -a`) by the per-turn dispatcher so every
runtime CLI inherits the credential, session, and Environment env vars.
A quoting or precedence bug here silently leaks raw shell metacharacters
into the runtime's process environment, so the body builder lives in its
own pure module — direct-testable under mutmut's hammett runner without
pulling in the Sprite or ORM dependencies that `_write_env_file` carries.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .specs import SessionSpec


def build_env_file_body(spec: "SessionSpec", credentials: list[tuple[str, str]]) -> str:
    """Render the body of /tmp/aod-env.

    `credentials` is a list of ``(env_var_name, value)`` pairs already
    resolved from the ORM by the caller. Each value is ``shlex.quote``-d
    before emission. Precedence: credentials → AOD_SESSION_ID → AOD_MODEL
    → sorted ``spec.environment.env_vars`` (per-environment overrides
    win). Body always ends with exactly one trailing newline.
    """
    lines: list[str] = []
    for env_name, value in credentials:
        lines.append(f"{env_name}={shlex.quote(value)}")
    # Falsy session_id / model mean "not set yet" and skip the line; a falsy
    # env_vars value is a deliberate user-supplied empty override and is
    # always emitted (e.g. `FOO=''`).
    if spec.runtime_session_id:
        lines.append(f"AOD_SESSION_ID={shlex.quote(spec.runtime_session_id)}")
    if spec.model:
        lines.append(f"AOD_MODEL={shlex.quote(spec.model)}")
    env = spec.environment
    if env is not None:
        for key, value in sorted((env.env_vars or {}).items()):
            lines.append(f"{key}={shlex.quote(value)}")
    return "\n".join(lines) + "\n"
