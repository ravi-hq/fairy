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

The actual per-turn body (threading, queue drain, finalize) lives in
`TurnExecutor` (`turn_executor.py`); the producer-consumer log plumbing
lives in `LogChunkSink` (`log_sink.py`). Both can be unit-tested without
the procrastinate decorator.

Retry policy: **none**. Turn-level retries against a Sprite that may be
half-torn-down would not heal anything; failures surface as session status
`failed` and the caller starts a new session. Provision failures follow the
same rule — a half-provisioned Sprite is torn down and the session is
marked `failed`. Destroy failures are log-and-swallow, matching the
pre-existing `best_effort_delete` contract.
"""

from __future__ import annotations

import logging

import posthog
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app

from agent_on_demand.analytics import capture as posthog_capture
from agent_on_demand.models import (
    AgentSession,
    AgentSessionLog,
    SessionTurn,
)
from agent_on_demand.observability import get_tracer

from .errors import NoBackendCredentialsError, ProvisionError, SessionHandleNotFound
from .provisioning import (
    destroy_session,
    provision_session,
    resume_session,
)
from .specs import build_spec_for_session
from .tracing import inject_carrier, traced_task
from .turn_executor import TurnExecutor

logger = logging.getLogger(__name__)


# `@procrastinate_app.task` must wrap `@traced_task` so the registered task body
# is the traced wrapper, not the bare function. `_otel_carrier` is consumed and
# stripped by `@traced_task`; declared explicitly so `inspect.signature(...)`
# (which follows `functools.wraps.__wrapped__`) reflects the real call surface.
@procrastinate_app.task(queue="sessions", name="provision_session", pass_context=False)
@traced_task("provision_session")
def provision_session_task(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
    _otel_carrier: dict | None = None,
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
        except Exception as e:
            # Without this, an unexpected error (Sprites SDK fault, DB
            # blip, anything other than the two typed errors above)
            # propagates out and Procrastinate marks the job failed —
            # but the session row stays `pending` forever. The API
            # client polls a session that will never complete.
            span.set_attribute("aod.failure_stage", "unexpected")
            logger.exception("unexpected provision failure for session %s", session_id)
            _mark_provision_failed(session, turn_id, f"unexpected error: {e}")
            raise

    execute_turn.defer(
        session_id=session_id,
        turn_id=turn_id,
        prompt=prompt,
        mode=mode,
        timeout=timeout,
        _otel_carrier=inject_carrier(),
    )


def _mark_provision_failed(session: AgentSession, turn_id: int, message: str) -> None:
    """Record a provision failure: log stderr chunk, mark session + turn failed,
    clear backend_handle (nothing was left behind — provision_session deletes on
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
        session.backend_handle = ""
        session.save(update_fields=["status", "backend_handle", "updated_at"])

    SessionTurn.objects.filter(pk=turn_id).update(
        status="failed",
        ended_at=now,
    )

    posthog_capture(
        session.user,
        "session.provision_failed",
        properties={
            "session_id": str(session.id),
            "runtime": session.runtime,
            "message": message,
        },
    )


def _fail_pending_turn(session: AgentSession, turn: SessionTurn, message: str) -> None:
    """Mark a session and turn as failed when they are still in pending state
    (before TurnExecutor has set them to running).

    Used when build_spec_for_session or resume_session fail before
    `TurnExecutor.run` is entered."""
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


# Decorator order + `_otel_carrier`: see note on `provision_session_task` above.
@procrastinate_app.task(queue="sessions", name="execute_turn", pass_context=False)
@traced_task("execute_turn")
def execute_turn(
    *,
    session_id: str,
    turn_id: int,
    prompt: str,
    mode: str,
    timeout: float,
    _otel_carrier: dict | None = None,
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
            handle = resume_session(session.user, session.backend_handle)
        except SessionHandleNotFound as e:
            logger.warning("sprite not found for session %s turn %s: %s", session_id, turn_id, e)
            span.set_attribute("aod.failure_stage", "sprite_not_found")
            _fail_pending_turn(session, turn, str(e))
            return
        TurnExecutor(session, turn, spec, handle, prompt, mode, timeout, span).run()


# Decorator order + `_otel_carrier`: see note on `provision_session_task` above.
@procrastinate_app.task(queue="sessions", name="destroy_session", pass_context=False)
@traced_task("destroy_session")
def destroy_session_task(*, user_id: int, handle: str, _otel_carrier: dict | None = None) -> None:
    """Delete a backend session on the worker. Best-effort — failures are
    logged, not retried, matching the pre-existing `destroy_session` contract.

    User resolution lives inside the task (rather than the view passing a
    client/token) so there's nothing sensitive on the Procrastinate queue.
    If the user row is gone by the time the worker picks up, we skip
    cleanup — the backend resource will eventually time out server-side.
    """
    close_old_connections()
    try:
        with posthog.new_context(capture_exceptions=True):
            posthog.tag("task", "destroy_session")
            posthog.tag("user_id", user_id)
            posthog.tag("handle", handle)
            User = get_user_model()
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                logger.warning(
                    "destroy_session_task: user %s gone, skipping handle %s",
                    user_id,
                    handle,
                )
                return
            destroy_session(user, handle)
    finally:
        close_old_connections()
