"""Procrastinate tasks for session lifecycle.

Two tasks live here, both running in the worker process:

- `provision_session_task` creates the Sprite and runs setup stages, then
  enqueues the first turn. Moving this off the web process is what keeps
  `POST /sessions` snappy — provisioning is the slow step.
- `execute_turn` drives one turn of the agent: DB state machine, blocking
  SDK call, log chunk persistence, finalize.

The inner daemon thread that wraps `sprite.command().run()` in `execute_turn`
stays here — it's load-bearing for the producer-consumer pattern against the
SDK's own event-loop thread (which pushes log chunks into a queue via
`TaggingQueueWriter`). Eliminating it is a separate optimization.

Retry policy: **none**. Turn-level retries against a Sprite that may be
half-torn-down would not heal anything; failures surface as session status
`failed` and the caller starts a new session. Provision failures follow the
same rule — a half-provisioned Sprite is torn down and the session is
marked `failed`.
"""

from __future__ import annotations

import io
import logging
import queue
import threading

import posthog
from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app
from sprites import ExecError

from agent_on_demand.models import (
    AgentSession,
    AgentSessionLog,
    SessionTurn,
    UserRuntimeKey,
)
from agent_on_demand.observability import get_tracer
from agent_on_demand.runtimes import RUNTIMES, RuntimeConfig

from .errors import NoSpritesKeyError, ProvisionError
from .provisioning import provision_session, resume_session
from .specs import McpServerSpec, RepoSpec, SessionSpec, SkillSpec

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
    session = AgentSession.objects.select_related("user", "agent", "environment").get(
        pk=session_id
    )
    # If the client terminated before the worker picked this up, skip.
    if session.status == "terminated":
        return

    spec = _build_spec_for_session(session)
    if spec is None:
        _mark_provision_failed(
            session, turn_id, f"No API key configured for runtime: {session.runtime}"
        )
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
            provision_session(session.user, spec)
        except NoSpritesKeyError as e:
            span.set_attribute("aod.failure_stage", "no_sprites_key")
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


def _build_spec_for_session(session: AgentSession) -> SessionSpec | None:
    """Rehydrate a SessionSpec from persisted session state. Returns None when
    the user no longer has an API key configured for the session's runtime."""
    api_key = UserRuntimeKey.get_key_for(session.user, session.runtime)
    if api_key is None:
        return None

    agent = session.agent
    mcp_servers: list[McpServerSpec] = []
    skills: list[SkillSpec] = []
    if agent is not None:
        for s in agent.mcp_servers or []:
            mcp_servers.append(
                McpServerSpec(
                    name=s["name"],
                    type=s.get("type", "url"),
                    url=s.get("url", ""),
                    headers=s.get("headers", {}),
                    command=s.get("command", ""),
                    args=s.get("args", []),
                    env=s.get("env", {}),
                )
            )
        for s in agent.skills or []:
            skills.append(SkillSpec(name=s["name"], content=s["content"]))

    repos = [
        RepoSpec(url=r.url, mount_path=r.mount_path, token=r.get_token())
        for r in session.resources.all()
    ]

    return SessionSpec(
        name=session.sprite_name,
        runtime=RUNTIMES[session.runtime],
        api_key=api_key,
        runtime_session_id=str(session.runtime_session_id)
        if session.runtime_session_id
        else None,
        environment=session.environment,
        repos=repos,
        mcp_servers=mcp_servers,
        skills=skills,
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
        session.save(update_fields=["status", "sprite_name", "updated_at"])

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
        sprite = resume_session(session.user, session.sprite_name)
        _execute_turn_body(session, turn, runtime, sprite, prompt, mode, timeout, span)


def _execute_turn_body(
    session, turn, runtime, sprite, prompt, mode, timeout, span
) -> None:

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
