import json
import time
from collections.abc import Generator

from agent_on_demand.models import AgentSession, AgentSessionLog


def stream_session_from_db(session_id: str) -> Generator[str, None, None]:
    """Yield SSE event strings by tailing the AgentSessionLog table.

    1. Replay all existing rows (emitting turn_start when the log's turn advances)
    2. Poll for new rows every 500ms
    3. Send heartbeat every 15s
    4. Stop when session is complete/failed AND no new rows remain
    """
    last_id = 0
    last_turn_id = None
    last_heartbeat = time.time()

    while True:
        chunks = list(
            AgentSessionLog.objects.filter(session_id=session_id, id__gt=last_id)
            .order_by("id")
            .values("id", "stream", "data", "turn_id", "turn__turn_number")[:100]
        )

        for chunk in chunks:
            last_id = chunk["id"]
            turn_id = chunk["turn_id"]
            if turn_id is not None and turn_id != last_turn_id:
                yield json.dumps({"type": "turn_start", "turn": chunk["turn__turn_number"]})
                last_turn_id = turn_id
            yield json.dumps(
                {
                    "type": "output",
                    "stream": chunk["stream"],
                    "data": chunk["data"],
                    "turn": chunk["turn__turn_number"],
                }
            )

        session = AgentSession.objects.get(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield json.dumps({"type": "terminated", "message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield json.dumps({"type": "error", "message": "Session failed"})
            else:
                yield json.dumps({"type": "exit", "code": session.exit_code})
            break

        now = time.time()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        time.sleep(0.5)
