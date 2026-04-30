"""Per-turn execution body extracted from `tasks.py`.

`TurnExecutor.run()` is the imperative orchestration that runs one turn of
an agent: it flips DB status to running, kicks off the runtime command in
a daemon thread, drains its output via `LogChunkSink`, joins the thread,
and finalizes session/turn rows.

Extracted from the procrastinate task body so the threading and DB-state
choreography is testable without the decorator dance.
"""

from __future__ import annotations

import logging
import threading

from django.utils import timezone

from agent_on_demand.analytics import capture as posthog_capture
from agent_on_demand.models import AgentSession, AgentSessionLog
from agent_on_demand.observability import get_tracer

from .log_sink import LogChunkSink
from .provisioning import STAGE_RUNTIME_START, emit_stage_event
from .runtime_trace import RuntimeTraceEmitter
from .tracing import inject_carrier
from .turn.argv import build_turn_argv
from .turn.outcome import compute_final_status

logger = logging.getLogger(__name__)

CMD_THREAD_JOIN_TIMEOUT = 5.0


class TurnExecutor:
    """Run one turn against an already-resumed backend handle.

    Construction does no IO. `run()` performs the full sequence:
      1. Pre-execution refresh — abort cleanly if a concurrent
         `terminate_session` already committed.
      2. Flip session.status / turn.status to "running".
      3. Emit `STAGE_RUNTIME_START` after the status flip and before
         spawning the worker thread.
      4. Spawn a daemon thread that calls `handle.make_command(...).run()`,
         feeding stdout/stderr through `LogChunkSink`'s writers.
      5. Drain the sink until the worker thread signals completion.
      6. Join the thread (5s timeout — leaks emit a posthog event).
      7. Finalize session/turn rows, with a guard for the
         deleted-mid-turn race.
      8. Emit the final `session.<status>` posthog event.
    """

    def __init__(
        self,
        session,
        turn,
        spec,
        handle,
        prompt: str,
        mode: str,
        timeout: float,
        span,
    ):
        self._session = session
        self._turn = turn
        self._spec = spec
        self._handle = handle
        self._prompt = prompt
        self._mode = mode
        self._timeout = timeout
        self._span = span
        self._trace_emitter = RuntimeTraceEmitter(span, spec.runtime.name, get_tracer())
        self._sink = LogChunkSink(session, turn, span=span, trace_emitter=self._trace_emitter)
        self._result_holder: list = []

    def run(self) -> None:
        started_at = timezone.now()

        if self._abort_pre_run(started_at):
            return

        self._mark_running(started_at)

        emit_stage_event(str(self._session.id), STAGE_RUNTIME_START, "started")

        # Built before thread spawn so any raise surfaces as a task-level
        # failure (procrastinate logging + alerting) rather than getting
        # swallowed into result_holder as a session-level error.
        #
        # `extra_env` carries the W3C trace context plus any OTel exporter
        # config the runtime wants Claude (or any other CLI) to see for the
        # turn. The carrier is captured here, while we are still inside the
        # `session.execute_turn` span, so the in-Sprite `claude_code.interaction`
        # span parents under it.
        carrier = inject_carrier()
        extra_env = self._spec.runtime.otel_env(
            self._spec,
            carrier.get("traceparent"),
            carrier.get("tracestate"),
        )
        argv = build_turn_argv(self._spec.runtime, self._spec, self._mode, extra_env=extra_env)

        cmd_thread = threading.Thread(target=self._run_command, args=(argv,), daemon=True)
        cmd_thread.start()

        self._sink.drain()
        self._sink.report_drops()
        self._trace_emitter.finish()

        cmd_thread.join(timeout=CMD_THREAD_JOIN_TIMEOUT)
        if cmd_thread.is_alive():
            self._report_thread_leak()

        self._finalize(started_at)

    def _abort_pre_run(self, now) -> bool:
        """Single pre-run guard for terminate-and-interrupt races.

        Catches two concurrent commits that landed after the task fetched
        the session row:

        - ``status="terminated"`` (terminate_session): mark turn failed,
          leave session terminated. Without this the unconditional saves
          below would overwrite the termination.
        - ``interrupt_requested=True`` (interrupt_session) before the
          turn actually started: mark turn ``interrupted`` and bring the
          session back to ``completed`` so the next prompt is legal. The
          flag is cleared so a follow-up turn doesn't inherit it.

        One refresh covers both — adding a second refresh would create
        an extra DB round-trip on every turn for a rare race.
        """
        self._session.refresh_from_db(fields=["status", "interrupt_requested"])
        if self._session.status == "terminated":
            AgentSessionLog.objects.create(
                session=self._session,
                turn=self._turn,
                stream="stderr",
                data="turn aborted: session terminated before execution started\n",
            )
            self._turn.status = "failed"
            self._turn.started_at = now
            self._turn.ended_at = now
            self._turn.save(update_fields=["status", "started_at", "ended_at"])
            return True
        if self._session.interrupt_requested:
            AgentSessionLog.objects.create(
                session=self._session,
                turn=self._turn,
                stream="stderr",
                data="turn aborted: interrupt requested before execution started\n",
            )
            self._turn.status = "interrupted"
            self._turn.started_at = now
            self._turn.ended_at = now
            self._turn.save(update_fields=["status", "started_at", "ended_at"])
            self._session.status = "completed"
            self._session.interrupt_requested = False
            self._session.save(update_fields=["status", "interrupt_requested", "updated_at"])
            return True
        return False

    def _mark_running(self, now) -> None:
        self._session.status = "running"
        self._session.save(update_fields=["status", "updated_at"])
        self._turn.status = "running"
        self._turn.started_at = now
        self._turn.save(update_fields=["status", "started_at"])

    def _run_command(self, argv: list[str]) -> None:
        # NOTE: if you add DB writes inside this inner thread, wrap the body
        # in close_old_connections()/finally. Today it only drives the SDK.
        try:
            cmd = self._handle.make_command(*argv, cwd="/home/sprite", timeout=self._timeout)
            cmd.set_input(self._prompt.encode("utf-8"))
            cmd.set_output(stdout=self._sink.stdout_writer, stderr=self._sink.stderr_writer)
            exit_code = cmd.run()
            self._result_holder.append(("exit", exit_code))
        except Exception as e:
            logger.exception(
                "session %s turn %s task raised", self._session.id, self._turn.turn_number
            )
            self._result_holder.append(("error", str(e)))
        finally:
            self._sink.put_sentinel()

    def _report_thread_leak(self) -> None:
        logger.error(
            "session %s turn %s: command thread still alive after join",
            self._session.id,
            self._turn.turn_number,
        )
        posthog_capture(
            self._session.user,
            "session.cmd_thread_leaked",
            properties={
                "session_id": str(self._session.id),
                "turn_number": self._turn.turn_number,
                "runtime": self._session.runtime,
            },
        )

    def _finalize(self, started_at) -> None:
        final_status, exit_code = compute_final_status(self._result_holder)
        ended = timezone.now()
        try:
            self._session.refresh_from_db(fields=["status", "interrupt_requested"])
        except AgentSession.DoesNotExist:
            # Session was deleted mid-turn (e.g. client raced terminate + delete).
            # Turn + logs were cascade-deleted alongside it; nothing left to write.
            logger.info(
                "execute_turn: session %s deleted mid-turn, skipping finalization",
                self._session.id,
            )
            return

        # An interrupt that landed during the turn overrides the natural
        # outcome: the in-Sprite process was killed at user request, so
        # the SDK exit code is incidental. The session goes back to
        # `completed` (not `failed`) so the caller can immediately send
        # a new prompt against the same Sprite.
        interrupted = self._session.interrupt_requested and self._session.status != "terminated"
        if interrupted:
            final_status_for_session = "completed"
            final_status_for_turn = "interrupted"
        else:
            final_status_for_session = final_status
            final_status_for_turn = final_status

        if self._session.status != "terminated":
            self._session.status = final_status_for_session
            self._session.exit_code = exit_code
            # Always clear `interrupt_requested` and include it in
            # `update_fields`, even on the "natural completion" branch.
            # Otherwise this race leaks: the refresh above reads False,
            # the view then commits True before the save below, and
            # because the save's `update_fields` skipped the column, the
            # True survives in the DB. The next /prompt's pre-run guard
            # would then see the stale True and abort the next (innocent)
            # turn as `interrupted`. Resetting unconditionally costs one
            # extra column in the UPDATE and closes the window — the
            # in-memory False overwrites whatever the view wrote.
            self._session.interrupt_requested = False
            self._session.save(
                update_fields=[
                    "status",
                    "exit_code",
                    "interrupt_requested",
                    "updated_at",
                ]
            )

        self._turn.status = final_status_for_turn
        self._turn.exit_code = exit_code
        self._turn.ended_at = ended
        self._turn.save(update_fields=["status", "exit_code", "ended_at"])

        duration_seconds = (ended - started_at).total_seconds()
        event_status = final_status_for_turn
        self._span.set_attribute("aod.final_status", event_status)
        if exit_code is not None:
            self._span.set_attribute("aod.exit_code", exit_code)
        self._span.set_attribute("aod.duration_seconds", duration_seconds)

        posthog_capture(
            self._session.user,
            f"session.{event_status}",
            properties={
                "session_id": str(self._session.id),
                "turn_number": self._turn.turn_number,
                "runtime": self._session.runtime,
                "exit_code": exit_code,
                "duration_seconds": duration_seconds,
                "mode": self._mode,
            },
        )
