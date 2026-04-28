"""Session provisioning: orchestrator, per-stage helpers, and the pure
script-building functions that compose the bash payload.

The orchestrator is the public entry point — call sites reach
`provision_session`, `resume_session`, `destroy_session` through this
package. Stage event names (`STAGE_*`) and `stage_timer` are re-exported
for `tasks.py` and tests that emit/inspect provisioning telemetry.
"""

from .events import (
    STAGE_CREATE_SPRITE,
    STAGE_ENV_FILE,
    STAGE_GIT_CREDENTIALS,
    STAGE_INSTALL_RUNTIME,
    STAGE_NETWORK_POLICY,
    STAGE_PROVISION_SETUP,
    STAGE_RUNTIME_CONFIG,
    STAGE_RUNTIME_START,
    STAGE_SKILLS,
    emit_stage_event,
    stage_timer,
)
from .orchestrator import destroy_session, provision_session, resume_session

__all__ = [
    "STAGE_CREATE_SPRITE",
    "STAGE_ENV_FILE",
    "STAGE_GIT_CREDENTIALS",
    "STAGE_INSTALL_RUNTIME",
    "STAGE_NETWORK_POLICY",
    "STAGE_PROVISION_SETUP",
    "STAGE_RUNTIME_CONFIG",
    "STAGE_RUNTIME_START",
    "STAGE_SKILLS",
    "destroy_session",
    "emit_stage_event",
    "provision_session",
    "resume_session",
    "stage_timer",
]
