import asyncio
import time
from collections.abc import AsyncGenerator

from agent_on_demand.models import AgentSession, AgentSessionLog
from agent_on_demand.stream_format import (
    format_chunk_events,
    format_event,
    format_terminal_event,
)

STREAM_IDLE_LIMIT = 600


async def stream_session_from_db(session_id: str, since: int = 0) -> AsyncGenerator[str, None]:
    """Yield SSE event strings by tailing AgentSessionLog.

    - Starts after `since` (exclusive). 0 = from the beginning.
    - Stale cursors silently resume from the next surviving row.
    - Exits on terminal status with no new rows, or after STREAM_IDLE_LIMIT
      seconds of no chunks.
    """
    last_id = since
    last_turn_id: int | None = None
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
            events, last_turn_id = format_chunk_events(chunk, last_turn_id)
            for event in events:
                yield event

        session = await AgentSession.objects.aget(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            terminal = format_terminal_event(session.status, session.exit_code, last_id)
            if terminal is not None:
                yield terminal
            break

        now = time.monotonic()
        if now - last_chunk_time > STREAM_IDLE_LIMIT:
            yield format_event("stale", last_id, {"message": f"No output for {STREAM_IDLE_LIMIT}s"})
            break

        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        await asyncio.sleep(0.5)
