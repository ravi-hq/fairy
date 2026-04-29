"""Build the per-turn argv handed to `sprite.command(...)`.

A quoting or ordering bug here silently breaks env-var sourcing for every
turn against every runtime, so the function lives in its own module so it
can be direct-tested under mutmut's hammett runner (which can't load the
Procrastinate task decorators in tasks.py).
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from agent_on_demand.runtimes import Runtime

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_turn_argv(
    runtime: Runtime,
    spec: SessionSpec,
    mode: str,
    extra_env: dict[str, str] | None = None,
) -> list[str]:
    """Return the full argv for the per-turn `sprite.command`.

    The first three elements are a thin `bash -lc` shim that sources
    `/tmp/aod-env` (so credential and Environment env vars reach the
    runtime CLI's process) and then execs the runtime argv verbatim. No
    template substitution — the runtime's `build_command` already inlined
    any session-id or model values from the spec.

    Prompt delivery is out of band: callers attach `cmd.stdin` with the
    prompt bytes so it flows through the bash shim into the runtime CLI.

    ``extra_env`` is a per-turn map of additional env vars to export into
    the runtime CLI's process. Used for telemetry context that changes per
    turn (e.g. W3C ``TRACEPARENT``) and so does not belong in the shared
    ``/tmp/aod-env`` file. Values are ``shlex.quote``-d. Keys are emitted
    in sorted order — a deterministic shim string is what mutation tests
    can pin against. Assignments happen *after* ``source /tmp/aod-env``,
    so they win against any duplicate set there. Keys are emitted
    verbatim and must already be valid shell identifiers; the caller (a
    ``Runtime.otel_env`` impl) is the trust boundary.

    The `env=` parameter on `sprite.command()` is deliberately not used:
    it replaces PATH/HOME on the Sprite (see sprites skill gotcha #1) and
    leaks values into the WebSocket URL (#2). Argv flows through the
    message body, so values inlined into the shim string stay out of URL
    logs.

    Raises ``ValueError`` if ``mode`` is not exactly ``"run"`` or
    ``"continue"``, with the message ``"mode must be 'run' or 'continue',
    got <repr>"``. Catches typos at the boundary instead of forwarding
    them to the runtime CLI as opaque flags.
    """
    # Runtime check (not `typing.cast`) so the literal tuple is a real
    # surface — don't "simplify" this back into a cast.
    if mode not in ("run", "continue"):
        raise ValueError(f"mode must be 'run' or 'continue', got {mode!r}")
    argv = runtime.build_command(spec, mode)  # type: ignore[arg-type]
    return ["bash", "-lc", build_env_source_shim(extra_env), "--", *argv]


def build_env_source_shim(extra_env: dict[str, str] | None) -> str:
    """Render the bash shim that sources `/tmp/aod-env` and re-execs the
    runtime argv. ``extra_env`` keys are emitted in sorted order between
    the source and the ``exec``."""
    parts = ["set -a", "source /tmp/aod-env"]
    if extra_env:
        for key in sorted(extra_env):
            parts.append(f"{key}={shlex.quote(extra_env[key])}")
    parts.append("set +a")
    parts.append('exec "$@"')
    return "; ".join(parts)


# The shim emitted when no `extra_env` is supplied. Pinned as a module
# constant so existing tests (and any future caller that wants to assert
# on the baseline shape) have a stable reference. Computed once via the
# same builder so the two can never drift.
_ENV_SOURCE_SHIM = build_env_source_shim(None)
