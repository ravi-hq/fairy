import asyncio
import json
import time
from collections.abc import AsyncGenerator

from agent_on_demand.models import AgentSession, AgentSessionLog

STREAM_IDLE_LIMIT = 600


def _format(event_type: str, log_id: int, payload: dict) -> str:
    return json.dumps({"type": event_type, "id": log_id, **payload})


async def stream_session_from_db(session_id: str, since: int = 0) -> AsyncGenerator[str, None]:
    """Yield SSE event strings by tailing AgentSessionLog.

    - Starts after `since` (exclusive). 0 = from the beginning.
    - Stale cursors silently resume from the next surviving row.
    - Exits on terminal status with no new rows, or after STREAM_IDLE_LIMIT
      seconds of no chunks.
    """
    last_id = since
    last_turn_id = None
    last_heartbeat = time.monotonic()
    last_chunk_time = time.monotonic()

    while True:
        chunks = [
            row
            async for row in AgentSessionLog.objects.filter(session_id=session_id, id__gt=last_id)
            .order_by("id")
            .values(
                "id",
                "kind",
                "stream",
                "data",
                "stage",
                "state",
                "duration_ms",
                "turn_id",
                "turn__turn_number",
            )[:100]
        ]

        if chunks:
            last_chunk_time = time.monotonic()

        for chunk in chunks:
            last_id = chunk["id"]
            if chunk["kind"] == "stage":
                payload = {"stage": chunk["stage"], "state": chunk["state"]}
                if chunk["duration_ms"] is not None:
                    payload["duration_ms"] = chunk["duration_ms"]
                if chunk["state"] == "failed" and chunk["data"]:
                    payload["message"] = chunk["data"]
                yield _format("stage", chunk["id"], payload)
                continue
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

        session = await AgentSession.objects.aget(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield _format("terminated", last_id, {"message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield _format("error", last_id, {"message": "Session failed"})
            else:
                yield _format("exit", last_id, {"code": session.exit_code})
            break

        now = time.monotonic()
        if now - last_chunk_time > STREAM_IDLE_LIMIT:
            yield _format("stale", last_id, {"message": f"No output for {STREAM_IDLE_LIMIT}s"})
            break

        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        await asyncio.sleep(0.5)
