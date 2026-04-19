"""Per-turn entry point: spawn the background execution thread.

The prompt flows in via the Sprites stdin frame (see `run_session_background`),
so no per-turn filesystem write is needed. The runtime-CLI invocation is
assembled inline per turn.
"""

from __future__ import annotations

import threading

from sprites import Sprite

from agent_on_demand.models import AgentSession, SessionTurn
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.stream import run_session_background


def run_turn(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Launch the background execution thread for this turn.

    Views call this for both turn 1 (`mode="run"`) and subsequent turns
    (`mode="continue"`). The prompt streams over the Sprites stdin frame.
    """
    runtime = RUNTIMES[session.runtime]
    thread = threading.Thread(
        target=run_session_background,
        args=(session, turn, sprite, runtime, prompt, mode, timeout),
        daemon=True,
    )
    thread.start()
