"""Procrastinate tasks for session lifecycle.

Three tasks live here, all running in the worker process:

- `provision_session_task` creates the Sprite and runs setup stages, then
  enqueues the first turn. Moving this off the web process is what keeps
  `POST /sessions` snappy — provisioning is the slow step.
- `execute_turn` drives one turn of the agent: DB state machine, blocking
  SDK call, log chunk persistence, finalize.
- `destroy_session_task` deletes the Sprite behind `POST /terminate` and
  `DELETE /sessions/{id}`. The Sprites delete call can take ~1s; moving
  it off the web process keeps those endpoints snappy.

The inner daemon thread that wraps `sprite.command().run()` in `execute_turn`
stays here — it's load-bearing for the producer-consumer pattern against the
SDK's own event-loop thread (which pushes log chunks into a queue via
`TaggingQueueWriter`). Eliminating it is a separate optimization.

Retry policy: **none**. Turn-level retries against a Sprite that may be
half-torn-down would not heal anything; failures surface as session status
`failed` and the caller starts a new session. Provision failures follow the
same rule — a half-provisioned Sprite is torn down and the session is
marked `failed`. Destroy failures are log-and-swallow, matching the
pre-existing `best_effort_delete` contract.
"""

from __future__ import annotations

import io
import logging
import queue
import threading
import time

import posthog
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app

from agent_on_demand.models import (
    AgentSession,
    AgentSessionLog,
    SessionTurn,
)
from agent_on_demand.observability import get_tracer

from .errors import NoBackendCredentialsError, ProvisionError, SessionHandleNotFound
from .provisioning import (
    STAGE_RUNTIME_START,
    destroy_session,
    emit_stage_event,
    provision_session,
    resume_session,
)
from .spec_factory import build_spec_for_session
from .turn_argv import build_turn_argv
from .turn_outcome import compute_final_status

logger = logging.getLogger(__name__)

_SENTINEL = object()
FLUSH_SIZE = 20
_BULK_CREATE_DELAYS = (0.1, 0.3, 1.0)


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
        self.drop_count = 0

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        try:
            self._queue.put(TaggedChunk(self._stream, data), timeout=5.0)
        except queue.Full:
            self.drop_count += 1
            if self.drop_count == 1:
                logger.warning("TaggingQueueWriter: output queue full, dropping chunks")
        return len(data)


@procrastinate_app.task(queue="sessions", name="provision_session", pass_context=False)
def provision_session_task(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    """Provision the Sprite on the worker, then enqueue the first turn.

    The session and turn rows already exist in the DB (created by the view in
    `pending` state). On success we chain into `execute_turn`. On any
    provision failure we mark the session + turn `failed` and stop; the
    client sees the outcome via `GET /sessions/{id}` or the stream endpoint.
    """
    close_old_connections()
    try:
        with posthog.new_context(capture_exceptions=True):
            posthog.tag("task", "provision_session")
            posthog.tag("session_id", session_id)
            posthog.tag("turn_id", turn_id)
            _provision_session_inner(
                session_id=session_id,
                turn_id=turn_id,
                prompt=prompt,
                mode=mode,
                timeout=timeout,
            )
    finally:
        close_old_connections()


def _provision_session_inner(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
) -> None:
    try:
        session = AgentSession.objects.select_related("user", "agent", "environment").get(
            pk=session_id
        )
    except AgentSession.DoesNotExist:
        logger.info("provision_session_task: session %s gone, skipping", session_id)
        return
    # If the client terminated before the worker picked this up, skip.
    if session.status == "terminated":
        return

    try:
        spec = build_spec_for_session(session)
    except Exception as e:
        logger.exception("failed to build SessionSpec for session %s", session_id)
        _mark_provision_failed(session, turn_id, f"internal error: {e}")
        return

    tracer = get_tracer()
    with tracer.start_as_current_span(
        "session.provision_task",
        attributes={
            "aod.session_id": session_id,
            "aod.runtime": session.runtime,
        },
    ) as span:
        try:
            provision_session(session.user, spec, session_id=session_id)
        except NoBackendCredentialsError as e:
            span.set_attribute("aod.failure_stage", "no_backend_credentials")
            _mark_provision_failed(session, turn_id, str(e))
            return
        except ProvisionError as e:
            span.set_attribute("aod.failure_stage", e.stage)
            logger.warning("provision failed at stage=%s: %s", e.stage, e)
            _mark_provision_failed(session, turn_id, str(e))
            return

    execute_turn.defer(
        session_id=session_id,
        turn_id=turn_id,
        prompt=prompt,
        mode=mode,
        timeout=timeout,
    )


def _mark_provision_failed(session: AgentSession, turn_id: int, message: str) -> None:
    """Record a provision failure: log stderr chunk, mark session + turn failed,
    clear sprite_name (nothing was left behind — provision_session deletes on
    failure), and emit the posthog event."""
    AgentSessionLog.objects.create(
        session=session,
        turn_id=turn_id,
        stream="stderr",
        data=f"provision failed: {message}\n",
    )
    now = timezone.now()

    session.refresh_from_db(fields=["status"])
    if session.status != "terminated":
        session.status = "failed"
        session.sprite_name = ""
        session.backend_handle = ""
        session.save(update_fields=["status", "sprite_name", "backend_handle", "updated_at"])

    SessionTurn.objects.filter(pk=turn_id).update(
        status="failed",
        ended_at=now,
    )

    with posthog.new_context():
        posthog.identify_context(str(session.user_id))
        posthog.capture(
            "session.provision_failed",
            properties={
                "session_id": str(session.id),
                "runtime": session.runtime,
                "message": message,
            },
        )


def _fail_pending_turn(session: AgentSession, turn: SessionTurn, message: str) -> None:
    """Mark a session and turn as failed when they are still in pending state
    (before _execute_turn_body has set them to running).

    Used when build_spec_for_session or resume_session fail before
    _execute_turn_body is entered."""
    AgentSessionLog.objects.create(
        session=session,
        turn=turn,
        stream="stderr",
        data=f"turn failed: {message}\n",
    )
    now = timezone.now()
    session.refresh_from_db(fields=["status"])
    if session.status != "terminated":
        session.status = "failed"
        session.save(update_fields=["status", "updated_at"])
    turn.status = "failed"
    turn.ended_at = now
    turn.save(update_fields=["status", "ended_at"])


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
        with posthog.new_context(capture_exceptions=True):
            posthog.tag("task", "execute_turn")
            posthog.tag("session_id", session_id)
            posthog.tag("turn_id", turn_id)
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
    try:
        session = AgentSession.objects.select_related("user", "agent", "environment").get(
            pk=session_id
        )
        turn = SessionTurn.objects.get(pk=turn_id)
    except (AgentSession.DoesNotExist, SessionTurn.DoesNotExist):
        logger.info("execute_turn: session=%s turn=%s gone, skipping", session_id, turn_id)
        return
    try:
        spec = build_spec_for_session(session)
    except Exception as e:
        logger.exception("failed to build SessionSpec for session %s turn %s", session_id, turn_id)
        _fail_pending_turn(session, turn, f"internal error: {e}")
        return

    tracer = get_tracer()
    with tracer.start_as_current_span(
        "session.execute_turn",
        attributes={
            "aod.session_id": session_id,
            "aod.turn_number": turn.turn_number,
            "aod.runtime": session.runtime,
            "aod.mode": mode,
            "aod.prompt_length": len(prompt),
            "aod.timeout": timeout,
        },
    ) as span:
        try:
            handle = resume_session(session.user, session.backend_handle or session.sprite_name)
        except SessionHandleNotFound as e:
            logger.warning("sprite not found for session %s turn %s: %s", session_id, turn_id, e)
            span.set_attribute("aod.failure_stage", "sprite_not_found")
            _fail_pending_turn(session, turn, str(e))
            return
        _execute_turn_body(session, turn, spec, handle, prompt, mode, timeout, span)


def _execute_turn_body(session, turn, spec, handle, prompt, mode, timeout, span) -> None:
    output_q: queue.Queue = queue.Queue(maxsize=4096)
    db_buffer: list[AgentSessionLog] = []
    result_holder: list = []
    stdout_writer = TaggingQueueWriter(output_q, "stdout")
    stderr_writer = TaggingQueueWriter(output_q, "stderr")

    def _flush_buffer():
        if not db_buffer:
            return
        for attempt, delay in enumerate(_BULK_CREATE_DELAYS, 1):
            try:
                AgentSessionLog.objects.bulk_create(db_buffer)
                db_buffer.clear()
                return
            except Exception:
                if attempt == len(_BULK_CREATE_DELAYS):
                    logger.exception("bulk_create exhausted retries")
                    with posthog.new_context():
                        posthog.identify_context(str(session.user_id))
                        posthog.capture(
                            "session.log_write_retry_exhausted",
                            properties={
                                "session_id": str(session.id),
                                "turn_number": turn.turn_number,
                                "runtime": session.runtime,
                                "dropped_chunks": len(db_buffer),
                            },
                        )
                    raise
                close_old_connections()
                time.sleep(delay)

    argv = build_turn_argv(spec.runtime, spec, mode)

    def _run_command():
        # NOTE: if you add DB writes inside this inner thread, wrap the body
        # in close_old_connections()/finally. Today it only drives the SDK.
        try:
            cmd = handle.make_command(*argv, cwd="/home/sprite", timeout=timeout)
            cmd.set_input(prompt.encode("utf-8"))
            cmd.set_output(stdout=stdout_writer, stderr=stderr_writer)
            exit_code = cmd.run()
            result_holder.append(("exit", exit_code))
        except Exception as e:
            logger.exception("session %s turn %s task raised", session.id, turn.turn_number)
            result_holder.append(("error", str(e)))
        finally:
            output_q.put(_SENTINEL)

    now = timezone.now()

    # Guard against a concurrent terminate_session that committed
    # status="terminated" after _execute_turn_inner fetched the session row.
    # Without this check, the unconditional save below would overwrite the
    # termination, leaving the session showing "running" for the whole turn.
    session.refresh_from_db(fields=["status"])
    if session.status == "terminated":
        AgentSessionLog.objects.create(
            session=session,
            turn=turn,
            stream="stderr",
            data="turn aborted: session terminated before execution started\n",
        )
        turn.status = "failed"
        turn.started_at = now
        turn.ended_at = now
        turn.save(update_fields=["status", "started_at", "ended_at"])
        return

    session.status = "running"
    session.save(update_fields=["status", "updated_at"])
    turn.status = "running"
    turn.started_at = now
    turn.save(update_fields=["status", "started_at"])

    emit_stage_event(str(session.id), STAGE_RUNTIME_START, "started")

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

    total_drops = stdout_writer.drop_count + stderr_writer.drop_count
    if total_drops > 0:
        with posthog.new_context():
            posthog.identify_context(str(session.user_id))
            posthog.capture(
                "session.output_chunks_dropped",
                properties={
                    "session_id": str(session.id),
                    "turn_number": turn.turn_number,
                    "runtime": session.runtime,
                    "dropped_count": total_drops,
                },
            )

    cmd_thread.join(timeout=5.0)
    if cmd_thread.is_alive():
        logger.error(
            "session %s turn %s: command thread still alive after join",
            session.id,
            turn.turn_number,
        )
        with posthog.new_context():
            posthog.identify_context(str(session.user_id))
            posthog.capture(
                "session.cmd_thread_leaked",
                properties={
                    "session_id": str(session.id),
                    "turn_number": turn.turn_number,
                    "runtime": session.runtime,
                },
            )

    final_status, exit_code = compute_final_status(result_holder)

    ended = timezone.now()
    try:
        session.refresh_from_db(fields=["status"])
    except AgentSession.DoesNotExist:
        # Session was deleted mid-turn (e.g. client raced terminate + delete).
        # Turn + logs were cascade-deleted alongside it; nothing left to write.
        logger.info("execute_turn: session %s deleted mid-turn, skipping finalization", session.id)
        return
    if session.status != "terminated":
        session.status = final_status
        session.exit_code = exit_code
        session.save(update_fields=["status", "exit_code", "updated_at"])

    turn.status = final_status
    turn.exit_code = exit_code
    turn.ended_at = ended
    turn.save(update_fields=["status", "exit_code", "ended_at"])

    duration_seconds = (ended - now).total_seconds()
    span.set_attribute("aod.final_status", final_status)
    if exit_code is not None:
        span.set_attribute("aod.exit_code", exit_code)
    span.set_attribute("aod.duration_seconds", duration_seconds)

    with posthog.new_context():
        posthog.identify_context(str(session.user_id))
        posthog.capture(
            f"session.{final_status}",
            properties={
                "session_id": str(session.id),
                "turn_number": turn.turn_number,
                "runtime": session.runtime,
                "exit_code": exit_code,
                "duration_seconds": duration_seconds,
                "mode": mode,
            },
        )


@procrastinate_app.task(queue="sessions", name="destroy_session", pass_context=False)
def destroy_session_task(*, user_id: int, sprite_name: str) -> None:
    """Delete a Sprite on the worker. Best-effort — failures are logged,
    not retried, matching the pre-existing `destroy_session` contract.

    User resolution lives inside the task (rather than the view passing a
    client/token) so there's nothing sensitive on the Procrastinate queue.
    If the user row is gone by the time the worker picks up, we skip
    cleanup — the Sprite will eventually time out server-side.
    """
    close_old_connections()
    try:
        with posthog.new_context(capture_exceptions=True):
            posthog.tag("task", "destroy_session")
            posthog.tag("user_id", user_id)
            posthog.tag("sprite_name", sprite_name)
            User = get_user_model()
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                logger.warning(
                    "destroy_session_task: user %s gone, skipping Sprite %s",
                    user_id,
                    sprite_name,
                )
                return
            destroy_session(user, sprite_name)
    finally:
        close_old_connections()
