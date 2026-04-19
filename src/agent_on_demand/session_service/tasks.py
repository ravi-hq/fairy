"""Procrastinate task: execute one turn of an agent session.

This runs in the worker process, not the web process. Its job is to drive the
per-turn DB state machine, run the blocking SDK call, persist log chunks to
`AgentSessionLog`, and finalize.

The inner daemon thread that wraps `sprite.command().run()` stays here — it's
load-bearing for the producer-consumer pattern against the SDK's own
event-loop thread (which pushes log chunks into a queue via
`TaggingQueueWriter`). Eliminating it is a separate optimization.

Retry policy: **none**. Turn-level retries against a Sprite that may be
half-torn-down would not heal anything; failures surface as session status
`failed` and the caller starts a new session.
"""

from __future__ import annotations

import io
import logging
import queue
import threading

from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app
from sprites import ExecError

from agent_on_demand.models import AgentSession, AgentSessionLog, SessionTurn
from agent_on_demand.runtimes import RUNTIMES, RuntimeConfig

from .provisioning import resume_session

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


def build_turn_command(runtime: RuntimeConfig, mode: str) -> str:
    """Render the per-turn `bash -c` script.

    Per turn we (1) source /tmp/aod-env to expose the runtime API key,
    AOD_SESSION_ID, and any Environment.env_vars; (2) slurp stdin into
    $PROMPT so the runtime CLI can reference it via `-p "$PROMPT"`; (3)
    exec the mode-appropriate runtime CLI. The string contains no secrets
    (the API key sources at runtime from the env file) so it's safe to
    appear in Sprites server-side WS URL logs.
    """
    runtime_cmd = runtime.cmd if mode == "run" else runtime.continue_cmd
    return f"set -a; source /tmp/aod-env; set +a; PROMPT=$(cat); export PROMPT; exec {runtime_cmd}"


@procrastinate_app.task(queue="sessions", name="execute_turn", pass_context=False)
def execute_turn(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Run one turn. Arguments are JSON-serializable primitives; we re-fetch
    ORM rows and re-open the Sprite handle inside the task.

    Wraps the body with `close_old_connections()` since this runs on a
    worker thread managed by Procrastinate, not in Django's per-request
    connection lifecycle.
    """
    close_old_connections()
    try:
        _execute_turn_inner(
            session_id=session_id,
            turn_id=turn_id,
            prompt=prompt,
            mode=mode,
            timeout=timeout,
        )
    finally:
        close_old_connections()


def _execute_turn_inner(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    session = AgentSession.objects.select_related("user").get(pk=session_id)
    turn = SessionTurn.objects.get(pk=turn_id)
    runtime = RUNTIMES[session.runtime]
    sprite = resume_session(session.user, session.sprite_name)

    output_q: queue.Queue = queue.Queue(maxsize=4096)
    db_buffer: list[AgentSessionLog] = []
    result_holder: list = []

    def _flush_buffer():
        if db_buffer:
            AgentSessionLog.objects.bulk_create(db_buffer)
            db_buffer.clear()

    def _run_command():
        # NOTE: if you add DB writes inside this inner thread, wrap the body
        # in close_old_connections()/finally. Today it only drives the SDK.
        try:
            cmd = sprite.command(
                "bash",
                "-c",
                build_turn_command(runtime, mode),
                cwd="/home/sprite",
                timeout=timeout,
            )
            cmd.stdin = io.BytesIO(prompt.encode("utf-8"))
            cmd.stdout = TaggingQueueWriter(output_q, "stdout")
            cmd.stderr = TaggingQueueWriter(output_q, "stderr")
            cmd.run()
            result_holder.append(("exit", 0))
        except ExecError as e:
            result_holder.append(("exit", e.exit_code()))
        except Exception as e:
            logger.exception("session %s turn %s task raised", session.id, turn.turn_number)
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
    session.refresh_from_db(fields=["status"])
    if session.status != "terminated":
        session.status = final_status
        session.exit_code = exit_code
        session.save(update_fields=["status", "exit_code", "updated_at"])

    turn.status = final_status
    turn.exit_code = exit_code
    turn.ended_at = ended
    turn.save(update_fields=["status", "exit_code", "ended_at"])
