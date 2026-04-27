"""Build Codex's per-turn argv.

Extracted from `runtimes/codex.py` so the branching between the initial
``run`` invocation and the ``continue`` follow-up gets direct, sync-only
coverage under mutmut. The two argvs differ only by an inserted
``resume --last`` pair: in continue mode, ``codex exec resume --last ...``
tells Codex to reattach to the conversation it persisted on the previous
turn (Codex tracks its own session state on disk), so dropping or
reordering those two flags silently breaks multi-turn continuity without
changing the exit code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_codex_command(spec: SessionSpec, mode: Literal["run", "continue"]) -> list[str]:
    """Return the Codex CLI argv for a single turn.

    - ``mode="run"``: fresh invocation —
      ``codex exec --dangerously-bypass-approvals-and-sandbox --json``.
    - ``mode="continue"``: resume the prior conversation —
      ``codex exec resume --last --dangerously-bypass-approvals-and-sandbox --json``.
      The ``resume --last`` pair (in that order) is what makes Codex pick
      up its own persisted conversation state from the previous turn.

    `spec` is accepted to match the runtime protocol but is unused today;
    Codex doesn't take any model/session args on the command line.
    """
    if mode == "continue":
        return [
            "codex",
            "exec",
            "resume",
            "--last",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]
    return [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]
