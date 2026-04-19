"""Per-turn entry points: write the prompt, spawn the execution thread."""

from __future__ import annotations

import threading

from sprites import Sprite

from agent_on_demand.models import AgentSession, SessionTurn
from agent_on_demand.stream import run_session_background

from .provisioning import _write_prompt


def run_turn(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Write the per-turn prompt to the Sprite and launch the background
    execution thread. Views call this for both turn 1 (`mode="run"`) and
    subsequent turns (`mode="continue"`)."""
    _write_prompt(sprite, prompt)
    thread = threading.Thread(
        target=run_session_background,
        args=(session, turn, sprite, mode, timeout),
        daemon=True,
    )
    thread.start()
