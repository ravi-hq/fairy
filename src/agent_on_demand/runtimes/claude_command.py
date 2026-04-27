"""Build the per-turn argv for Claude Code's CLI.

Extracted from `runtimes/claude.py` so the argv shape can be
mutation-tested in isolation. The original `ClaudeRuntime.build_command`
is now a thin delegator. This module is pure (spec + mode in, list of
strings out, no I/O, no Django).

Each flag in the returned argv is load-bearing:

  - ``--dangerously-skip-permissions`` — Claude Code's permissions UI
    expects a TTY; on a Sprite there is no human to approve, so we opt
    out. Removing it strands every turn waiting on a prompt.
  - ``--print`` + ``--verbose`` + ``--output-format stream-json`` — the
    only combination that emits the per-message stream the worker tails
    into ``AgentSessionLog``. Drop any one and the SSE replay endpoint
    has nothing to show.
  - ``--resume`` (continue mode) vs ``--session-id`` (run mode) — Claude
    distinguishes "start a new run with this id" from "resume the run
    with this id". A swap silently replays every turn from scratch or
    forks a new conversation.
  - The trailing ``runtime_session_id or ""`` — when the caller hasn't
    yet allocated a session id we still need a positional placeholder so
    Claude's argv parser doesn't consume the next flag as the id. The
    empty string is intentional (not raised) because the same code path
    handles the first turn of a brand-new session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import SessionSpec


def build_claude_command(spec: SessionSpec, mode: Literal["run", "continue"]) -> list[str]:
    """Return the argv handed to ``sprite.command(...)`` for a Claude turn.

    ``mode="run"`` starts a new Claude conversation tagged with
    ``spec.runtime_session_id``; ``mode="continue"`` resumes the existing
    conversation with that id. The session id is always the final
    element so the calling shim ends with ``[..., "--<flag>", "<id>"]``.
    """
    session_id = spec.runtime_session_id or ""
    return [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--resume" if mode == "continue" else "--session-id",
        session_id,
    ]
