"""Per-turn entry point: enqueue a Procrastinate task.

The web process no longer runs session execution. It creates the DB rows and
defers the work onto the worker service; the task body lives in
`session_service.tasks.execute_turn`.
"""

from __future__ import annotations

from agent_on_demand.models import AgentSession, SessionTurn


def run_turn(
    session: AgentSession,
    turn: SessionTurn,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Enqueue a task to execute this turn on the worker service."""
    # Local import: `tasks` imports `turn.argv` / `turn.outcome`, so a
    # top-level import here would cycle through the `turn` package init.
    from agent_on_demand.session_service.tasks import execute_turn

    execute_turn.defer(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt=prompt,
        mode=mode,
        timeout=float(timeout),
    )
