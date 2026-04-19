import io
import json
import logging
import queue
import threading
import time
from collections.abc import Generator

from django.utils import timezone
from sprites import ExecError, Sprite

from agent_on_demand.models import AgentSession, AgentSessionLog, SessionTurn

logger = logging.getLogger(__name__)

_SENTINEL = object()

FLUSH_SIZE = 20


class TaggedChunk:
    """A chunk of output tagged with its stream name."""

    __slots__ = ("stream", "data")

    def __init__(self, stream: str, data: bytes):
        self.stream = stream
        self.data = data


class TaggingQueueWriter(io.RawIOBase):
    """A writable BinaryIO that puts tagged chunks into a queue.

    Each chunk is tagged with the stream name (stdout/stderr) so downstream
    consumers can distinguish them.
    """

    def __init__(self, q: queue.Queue, stream: str):
        self._queue = q
        self._stream = stream

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        self._queue.put(TaggedChunk(self._stream, data))
        return len(data)


def run_session_background(
    session: AgentSession,
    turn: SessionTurn,
    sprite: Sprite,
    mode: str,
    timeout: float,
):
    """Run one turn of an agent session in a background thread.

    `mode` is "run" for turn 1 and "continue" for subsequent turns. Logs are
    tagged with the turn so consumers can replay per-turn. The session's
    status tracks the latest turn for backward-compat.
    """
    output_q: queue.Queue = queue.Queue(maxsize=4096)
    db_buffer: list[AgentSessionLog] = []
    result_holder: list = []

    def _flush_buffer():
        if db_buffer:
            AgentSessionLog.objects.bulk_create(db_buffer)
            db_buffer.clear()

    def _run_command():
        try:
            cmd = sprite.command("bash", "/run-agent.sh", mode, timeout=timeout)
            cmd.stdout = TaggingQueueWriter(output_q, "stdout")
            cmd.stderr = TaggingQueueWriter(output_q, "stderr")
            cmd.run()
            result_holder.append(("exit", 0))
        except ExecError as e:
            # ExecError.exit_code is a method (not a @property like Sprite.exit_code)
            result_holder.append(("exit", e.exit_code()))
        except Exception as e:
            result_holder.append(("error", str(e)))
        finally:
            output_q.put(_SENTINEL)

    now = timezone.now()
    session.status = "running"
    session.save(update_fields=["status", "updated_at"])
    turn.status = "running"
    turn.started_at = now
    turn.save(update_fields=["status", "started_at"])

    cmd_thread = threading.Thread(target=_run_command, daemon=True)
    cmd_thread.start()

    while True:
        try:
            chunk = output_q.get(timeout=1.0)
        except queue.Empty:
            _flush_buffer()
            continue

        if chunk is _SENTINEL:
            break

        db_buffer.append(
            AgentSessionLog(
                session=session,
                turn=turn,
                stream=chunk.stream,
                data=chunk.data.decode("utf-8", errors="replace"),
            )
        )
        if len(db_buffer) >= FLUSH_SIZE:
            _flush_buffer()

    _flush_buffer()

    cmd_thread.join(timeout=5.0)

    if result_holder:
        kind, value = result_holder[0]
        if kind == "exit":
            final_status = "completed" if value == 0 else "failed"
            exit_code = value
        else:
            final_status = "failed"
            exit_code = None
    else:
        final_status = "failed"
        exit_code = None

    ended = timezone.now()
    # Don't clobber session.status if the session was terminated mid-run.
    session.refresh_from_db(fields=["status"])
    if session.status != "terminated":
        session.status = final_status
        session.exit_code = exit_code
        session.save(update_fields=["status", "exit_code", "updated_at"])

    turn.status = final_status
    turn.exit_code = exit_code
    turn.ended_at = ended
    turn.save(update_fields=["status", "exit_code", "ended_at"])


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
                yield json.dumps(
                    {"type": "turn_start", "turn": chunk["turn__turn_number"]}
                )
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
