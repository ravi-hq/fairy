import json
import time
from collections.abc import Generator

from agent_on_demand.models import AgentSession, AgentSessionLog

STREAM_IDLE_LIMIT = 600  # seconds of no new chunks before emitting `stale` and exiting


def _format(event_type: str, log_id: int, payload: dict) -> str:
    return json.dumps({"type": event_type, "id": log_id, **payload})


def stream_session_from_db(session_id: str, since: int = 0) -> Generator[str, None, None]:
    """Yield SSE event strings by tailing AgentSessionLog.

    - Starts after `since` (exclusive). 0 = from the beginning.
    - Stale cursors (log row deleted by retention) silently resume from the
      next surviving row — `id__gt=since` naturally handles this.
    - Exits when the session reaches a terminal state with no new rows, OR
      when no new chunks have arrived for STREAM_IDLE_LIMIT seconds.
    """
    last_id = since
    last_turn_id = None
    last_heartbeat = time.time()
    last_chunk_time = time.time()

    while True:
        chunks = list(
            AgentSessionLog.objects.filter(session_id=session_id, id__gt=last_id)
            .order_by("id")
            .values("id", "stream", "data", "turn_id", "turn__turn_number")[:100]
        )

        if chunks:
            last_chunk_time = time.time()

        for chunk in chunks:
            last_id = chunk["id"]
            turn_id = chunk["turn_id"]
            if turn_id is not None and turn_id != last_turn_id:
                yield _format("turn_start", chunk["id"], {"turn": chunk["turn__turn_number"]})
                last_turn_id = turn_id
            yield _format(
                "output",
                chunk["id"],
                {
                    "stream": chunk["stream"],
                    "data": chunk["data"],
                    "turn": chunk["turn__turn_number"],
                },
            )

        session = AgentSession.objects.get(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield _format("terminated", last_id, {"message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield _format("error", last_id, {"message": "Session failed"})
            else:
                yield _format("exit", last_id, {"code": session.exit_code})
            break

        if time.time() - last_chunk_time > STREAM_IDLE_LIMIT:
            yield _format(
                "stale",
                last_id,
                {"message": f"No output for {STREAM_IDLE_LIMIT}s"},
            )
            break

        now = time.time()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        time.sleep(0.5)
