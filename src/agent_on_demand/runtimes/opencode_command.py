"""Build the per-turn argv for the opencode CLI.

Extracted from `runtimes/opencode.py` so the argv construction can be
mutation-tested in isolation. The original `OpencodeRuntime.build_command`
keeps its public interface and delegates here.

Opencode is a meta-runtime: a single ``opencode`` binary fronts 75+
providers, and the provider+model pair is selected per invocation via
``--model provider/model_id``. Unlike claude/codex/gemini — whose argv
is model-agnostic and selects models elsewhere — opencode inlines
``spec.model`` into the argv verbatim.

When ``mode == "continue"``, ``--continue`` is appended as the trailing
flag so opencode resumes the prior conversation in the same workspace
instead of starting a fresh session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_opencode_command(spec: SessionSpec, mode: Literal["run", "continue"]) -> list[str]:
    """Return the opencode CLI argv for one turn.

    The model string is inlined verbatim from ``spec.model`` (opencode
    accepts ``provider/model_id``). ``--continue`` is appended only for
    ``mode == "continue"``; on ``"run"`` a fresh conversation is started.
    """
    argv = ["opencode", "run", "--model", spec.model, "--format", "json"]
    if mode == "continue":
        argv.append("--continue")
    return argv
