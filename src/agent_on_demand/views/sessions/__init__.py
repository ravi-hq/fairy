"""Sessions HTTP views.

Public surface preserved from the original single-file `views/sessions.py`:
view callables (`sessions_list_create`, `get_session`, `send_prompt`,
`list_session_turns`, `interrupt_session`, `terminate_session`,
`delete_session`, `stream_session`) and the request pydantic models
(`GitHubRepoResource`, `RunRequest`, `PromptRequest`).

Several auxiliary names (`session_service`, `check_can_accept_prompt`,
`stream_session_from_db`) are also re-exported because tests patch them at
this package's namespace and the view submodules look them up here at call
time.
"""

# Re-export auxiliary names that test code patches at this module's namespace.
# The view submodules dereference these via `_pkg.<name>` at call time so the
# patch is observed; callers outside the package may also import them here.
from agent_on_demand import session_service
from agent_on_demand.session_state import (
    check_can_accept_prompt,
    check_can_delete,
    check_can_interrupt,
    check_can_terminate,
)
from agent_on_demand.stream import stream_session_from_db

from .schemas import GitHubRepoResource, PromptRequest, RunRequest

# `tests/test_request_schema_coverage.py` discovers Request models by walking
# `inspect.getmembers(module)` and filtering on `obj.__module__ == module_path`.
# Re-exporting the schemas leaves their `__module__` pointing at `.schemas`,
# which would hide them from discovery. Rebrand to this package so the public
# import path and the discovery filter agree.
for _model in (GitHubRepoResource, RunRequest, PromptRequest):
    _model.__module__ = __name__
del _model

# View functions live in submodules; import them after the auxiliary names
# above are bound so the submodules' `_pkg` lookups resolve at call time.
from .create import sessions_list_create  # noqa: E402
from .lifecycle import (  # noqa: E402
    delete_session,
    get_session,
    interrupt_session,
    list_session_turns,
    send_prompt,
    terminate_session,
)
from .stream import stream_session  # noqa: E402

__all__ = [
    "GitHubRepoResource",
    "PromptRequest",
    "RunRequest",
    "check_can_accept_prompt",
    "check_can_delete",
    "check_can_interrupt",
    "check_can_terminate",
    "delete_session",
    "get_session",
    "interrupt_session",
    "list_session_turns",
    "send_prompt",
    "session_service",
    "sessions_list_create",
    "stream_session",
    "stream_session_from_db",
    "terminate_session",
]
