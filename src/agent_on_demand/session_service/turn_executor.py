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

import posthog
from django.utils import timezone

from agent_on_demand.models import AgentSession, AgentSessionLog

from .log_sink import LogChunkSink
from .provisioning import STAGE_RUNTIME_START, emit_stage_event
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
        self._sink = LogChunkSink(session, turn)
        self._result_holder: list = []

    def run(self) -> None:
        started_at = timezone.now()

        if self._abort_if_terminated(started_at):
            return

        self._mark_running(started_at)

        emit_stage_event(str(self._session.id), STAGE_RUNTIME_START, "started")

        cmd_thread = threading.Thread(target=self._run_command, daemon=True)
        cmd_thread.start()

        self._sink.drain()
        self._sink.report_drops()

        cmd_thread.join(timeout=CMD_THREAD_JOIN_TIMEOUT)
        if cmd_thread.is_alive():
            self._report_thread_leak()

        self._finalize(started_at)

    def _abort_if_terminated(self, now) -> bool:
        """Guard against a concurrent terminate_session that committed
        status="terminated" after the task fetched the session row.
        Without this check, the unconditional save below would overwrite
        the termination, leaving the session showing "running" for the
        whole turn."""
        self._session.refresh_from_db(fields=["status"])
        if self._session.status != "terminated":
            return False
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

    def _mark_running(self, now) -> None:
        self._session.status = "running"
        self._session.save(update_fields=["status", "updated_at"])
        self._turn.status = "running"
        self._turn.started_at = now
        self._turn.save(update_fields=["status", "started_at"])

    def _run_command(self) -> None:
        # NOTE: if you add DB writes inside this inner thread, wrap the body
        # in close_old_connections()/finally. Today it only drives the SDK.
        argv = build_turn_argv(self._spec.runtime, self._spec, self._mode)
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
        with posthog.new_context():
            posthog.identify_context(str(self._session.user_id))
            posthog.capture(
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
            self._session.refresh_from_db(fields=["status"])
        except AgentSession.DoesNotExist:
            # Session was deleted mid-turn (e.g. client raced terminate + delete).
            # Turn + logs were cascade-deleted alongside it; nothing left to write.
            logger.info(
                "execute_turn: session %s deleted mid-turn, skipping finalization",
                self._session.id,
            )
            return
        if self._session.status != "terminated":
            self._session.status = final_status
            self._session.exit_code = exit_code
            self._session.save(update_fields=["status", "exit_code", "updated_at"])

        self._turn.status = final_status
        self._turn.exit_code = exit_code
        self._turn.ended_at = ended
        self._turn.save(update_fields=["status", "exit_code", "ended_at"])

        duration_seconds = (ended - started_at).total_seconds()
        self._span.set_attribute("aod.final_status", final_status)
        if exit_code is not None:
            self._span.set_attribute("aod.exit_code", exit_code)
        self._span.set_attribute("aod.duration_seconds", duration_seconds)

        with posthog.new_context():
            posthog.identify_context(str(self._session.user_id))
            posthog.capture(
                f"session.{final_status}",
                properties={
                    "session_id": str(self._session.id),
                    "turn_number": self._turn.turn_number,
                    "runtime": self._session.runtime,
                    "exit_code": exit_code,
                    "duration_seconds": duration_seconds,
                    "mode": self._mode,
                },
            )
