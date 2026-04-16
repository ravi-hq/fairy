import io
import json
import logging
import queue
import threading
import time
from collections.abc import Generator

from sprites import ExecError, Sprite

from fairy.models import AgentSession, AgentSessionLog

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

    def write(self, b: bytes | bytearray) -> int:
        data = bytes(b)
        self._queue.put(TaggedChunk(self._stream, data))
        return len(data)


def run_session_background(
    session: AgentSession,
    sprite: Sprite,
    timeout: float,
):
    """Run agent in a background thread, writing output to the database.

    POST /run starts this and returns immediately. Output is persisted to
    AgentSessionLog rows.
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
            cmd = sprite.command("bash", "/run-agent.sh", timeout=timeout)
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

    # Update session status
    session.status = "running"
    session.save(update_fields=["status", "updated_at"])

    # Run the command in a sub-thread so we can drain the queue in this thread
    cmd_thread = threading.Thread(target=_run_command, daemon=True)
    cmd_thread.start()

    # Drain queue → DB
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
                stream=chunk.stream,
                data=chunk.data.decode("utf-8", errors="replace"),
            )
        )
        if len(db_buffer) >= FLUSH_SIZE:
            _flush_buffer()

    # Flush remaining
    _flush_buffer()

    cmd_thread.join(timeout=5.0)

    # Update session with result
    if result_holder:
        kind, value = result_holder[0]
        if kind == "exit":
            session.status = "completed" if value == 0 else "failed"
            session.exit_code = value
        else:
            session.status = "failed"
    else:
        session.status = "failed"

    session.save(update_fields=["status", "exit_code", "updated_at"])


def stream_session_from_db(session_id: str) -> Generator[str, None, None]:
    """Yield SSE event strings by tailing the AgentSessionLog table.

    1. Replay all existing rows
    2. Poll for new rows every 500ms
    3. Send heartbeat every 15s
    4. Stop when session is complete/failed AND no new rows remain
    """
    last_id = 0
    last_heartbeat = time.time()

    while True:
        chunks = list(
            AgentSessionLog.objects.filter(session_id=session_id, id__gt=last_id)
            .order_by("id")
            .values("id", "stream", "data")[:100]
        )

        for chunk in chunks:
            last_id = chunk["id"]
            yield json.dumps({
                "type": "output",
                "stream": chunk["stream"],
                "data": chunk["data"],
            })

        # Check if session is done
        session = AgentSession.objects.get(pk=session_id)
        if session.status in ("completed", "failed", "terminated") and not chunks:
            if session.status == "terminated":
                yield json.dumps({"type": "terminated", "message": "Session terminated"})
            elif session.status == "failed" and session.exit_code is None:
                yield json.dumps({"type": "error", "message": "Session failed"})
            else:
                yield json.dumps({"type": "exit", "code": session.exit_code})
            break

        # Heartbeat to keep connection alive
        now = time.time()
        if now - last_heartbeat >= 15:
            last_heartbeat = now
            yield ""

        time.sleep(0.5)
