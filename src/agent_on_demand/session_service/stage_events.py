"""Stage names + the timer/event helpers shared by `provisioning.py` and
`provisioning_stages.py`.

Lives in its own module so the orchestrator and the per-stage helpers
can both import it without forming a cycle. Stage names are emitted both
as `ProvisionError.stage` tags (server-side logging) and as `stage` SSE
events — see `site/docs/api/streaming.md` for the public schema.
"""

from __future__ import annotations

import contextlib
import time
from typing import Iterator

STAGE_CREATE_SPRITE = "create_sprite"
STAGE_INSTALL_RUNTIME = "install_runtime"
STAGE_NETWORK_POLICY = "network_policy"
STAGE_ENV_FILE = "env_file"
STAGE_GIT_CREDENTIALS = "git_credentials"
STAGE_PROVISION_SETUP = "provision_setup"
STAGE_RUNTIME_CONFIG = "runtime_config"
STAGE_SKILLS = "skills"
STAGE_RUNTIME_START = "runtime_start"


def emit_stage_event(
    session_id: str | None,
    stage: str,
    state: str,
    duration_ms: int | None = None,
    message: str = "",
) -> None:
    """Write a stage row to AgentSessionLog. No-op if session_id is None (unit
    tests that exercise provision_session directly without a real session row)."""
    if session_id is None:
        return
    from agent_on_demand.models import AgentSessionLog

    AgentSessionLog.objects.create(
        session_id=session_id,
        kind="stage",
        stage=stage,
        state=state,
        duration_ms=duration_ms,
        data=message,
    )


@contextlib.contextmanager
def stage_timer(session_id: str | None, stage: str) -> Iterator[None]:
    """Emit `started` entering and `done` on clean exit, or `failed` (with the
    exception message) on error. `duration_ms` is attached to done/failed."""
    emit_stage_event(session_id, stage, "started")
    start = time.monotonic()
    try:
        yield
    except Exception as e:
        emit_stage_event(
            session_id,
            stage,
            "failed",
            int((time.monotonic() - start) * 1000),
            message=str(e),
        )
        raise
    else:
        emit_stage_event(
            session_id,
            stage,
            "done",
            int((time.monotonic() - start) * 1000),
        )
