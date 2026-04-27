"""Build the per-turn argv handed to `sprite.command(...)`.

A quoting or ordering bug here silently breaks env-var sourcing for every
turn against every runtime, so the function lives in its own module so it
can be direct-tested under mutmut's hammett runner (which can't load the
Procrastinate task decorators in tasks.py).
"""

from __future__ import annotations

from typing import Literal, cast

from agent_on_demand.runtimes import Runtime

_ENV_SOURCE_SHIM = 'set -a; source /tmp/aod-env; set +a; exec "$@"'


def build_turn_argv(runtime: Runtime, spec, mode: str) -> list[str]:
    """Return the full argv for the per-turn `sprite.command`.

    The first three elements are a thin `bash -lc` shim that sources
    `/tmp/aod-env` (so credential and Environment env vars reach the
    runtime CLI's process) and then execs the runtime argv verbatim. No
    template substitution — the runtime's `build_command` already inlined
    any session-id or model values from the spec.

    Prompt delivery is out of band: callers attach `cmd.stdin` with the
    prompt bytes so it flows through the bash shim into the runtime CLI.
    """
    argv = runtime.build_command(spec, cast(Literal["run", "continue"], mode))
    return ["bash", "-lc", _ENV_SOURCE_SHIM, "--", *argv]
