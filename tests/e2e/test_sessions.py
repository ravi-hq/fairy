"""E2E tests for session lifecycle, streaming, termination, and multi-turn."""

import time

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique, stream_all_output

# Every test in this module spawns a real agent session — bucket them under
# @slow so `make test-e2e-fast` actually skips them.
pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Runtime parametrization
# ---------------------------------------------------------------------------


# Class-scoped so a single parametrization drives one shared session per class
# rather than re-spawning per test method.
@pytest.fixture(scope="class", params=list(RUNTIME_MODELS.keys()))
def runtime(request, e2e_runtimes):
    """Yield each runtime, skipping those not in E2E_RUNTIMES."""
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


def _create_throwaway_agent(api, runtime, label):
    """Create an agent and return (id, archive-callable). Used by class-scoped
    fixtures that need to manage their own teardown (the function-scoped
    `create_agent` fixture can't be reused at class scope)."""
    resp = api.create_agent(
        name=_unique(f"e2e-{label}-{runtime}"),
        model=RUNTIME_MODELS[runtime],
        runtime=runtime,
    )
    resp.raise_for_status()
    agent = resp.json()
    return agent, lambda: api.archive_agent(agent["id"])


def _start_throwaway_session(api, agent_id, prompt, timeout=120):
    resp = api.create_session(agent_id=agent_id, prompt=prompt, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Session lifecycle (per runtime)
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """One completed session per runtime, asserted against multiple ways."""

    @pytest.fixture(scope="class")
    def completed(self, api, runtime):
        agent, cleanup_agent = _create_throwaway_agent(api, runtime, "lifecycle")
        session = _start_throwaway_session(
            api,
            agent["id"],
            "Print exactly 'FAIRY_E2E_OK' to stdout. Do not create any files.",
        )
        initial = session
        final, events = api.run_session(session["id"])
        yield {
            "agent": agent,
            "initial": initial,
            "final": final,
            "events": events,
            "runtime": runtime,
        }
        try:
            api.terminate_session(session["id"])
        except Exception:
            pass
        try:
            api.delete_session(session["id"])
        except Exception:
            pass
        cleanup_agent()

    def test_initial_status_is_pending(self, completed):
        assert completed["initial"]["status"] == "pending"
        assert "id" in completed["initial"]

    def test_session_completes(self, completed):
        assert completed["final"]["status"] == "completed"
        assert completed["final"]["exit_code"] == 0

    def test_session_has_correct_runtime(self, completed):
        assert completed["final"]["runtime"] == completed["runtime"]

    def test_get_session_returns_metadata(self, api, completed):
        resp = api.get_session(completed["initial"]["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == completed["initial"]["id"]
        assert data["agent_id"] == completed["agent"]["id"]
        assert data["runtime"] == completed["runtime"]
        assert "created_at" in data
        assert "updated_at" in data


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestStreaming:
    """One completed session per runtime; assert across its captured events."""

    @pytest.fixture(scope="class")
    def streamed(self, api, runtime):
        agent, cleanup_agent = _create_throwaway_agent(api, runtime, "stream")
        session = _start_throwaway_session(
            api,
            agent["id"],
            "Print 'hello world' and exit immediately.",
        )
        final, events = api.run_session(session["id"])
        yield {
            "agent": agent,
            "session": session,
            "final": final,
            "events": events,
            "runtime": runtime,
        }
        try:
            api.terminate_session(session["id"])
        except Exception:
            pass
        try:
            api.delete_session(session["id"])
        except Exception:
            pass
        cleanup_agent()

    def test_stream_has_start_and_exit(self, streamed):
        types = [e["type"] for e in streamed["events"]]
        assert "start" in types, f"Missing 'start' event. Got: {types}"
        assert "exit" in types, f"Missing 'exit' event. Got: {types}"

    def test_start_event_fields(self, streamed):
        start = next(e for e in streamed["events"] if e["type"] == "start")
        assert start["runtime"] == streamed["runtime"]
        assert start["session_id"] == streamed["session"]["id"]

    def test_exit_event_code(self, streamed):
        exit_evt = next(e for e in streamed["events"] if e["type"] == "exit")
        assert exit_evt["code"] == 0

    def test_stream_contains_output(self, streamed):
        output_events = [e for e in streamed["events"] if e.get("type") == "output"]
        assert len(output_events) > 0, "No output events in stream"

    def test_stream_replay_after_completion(self, api, streamed):
        """Re-streaming a completed session should replay start/exit/output."""
        events = api.collect_stream(streamed["session"]["id"])
        types = [e["type"] for e in events]
        assert "start" in types
        assert "exit" in types
        assert any(e["type"] == "output" for e in events)


# ---------------------------------------------------------------------------
# Termination & deletion
# ---------------------------------------------------------------------------


class TestTermination:
    """Walk a single completed session through the full terminate-then-delete
    lifecycle, asserting at each step. Replaces five separate sessions with one.
    """

    def test_terminate_then_delete_lifecycle(self, api, runtime):
        agent, cleanup_agent = _create_throwaway_agent(api, runtime, "term")
        session = _start_throwaway_session(api, agent["id"], "Say ok.")
        try:
            final, _ = api.run_session(session["id"])
            assert final["status"] == "completed"

            # First terminate succeeds and flips status.
            resp = api.terminate_session(session["id"])
            assert resp.status_code == 200
            assert resp.json()["status"] == "terminated"

            # Second terminate is idempotent-error.
            resp = api.terminate_session(session["id"])
            assert resp.status_code == 409

            # Sending a prompt to a terminated session is rejected.
            resp = api.send_prompt(session["id"], prompt="follow up")
            assert resp.status_code == 409

            # Re-streaming a terminated session shows a terminated event.
            events = api.collect_stream(session["id"])
            types = [e["type"] for e in events]
            assert "terminated" in types

            # Delete succeeds; subsequent GET 404s.
            resp = api.delete_session(session["id"])
            assert resp.status_code == 200
            resp = api.get_session(session["id"])
            assert resp.status_code == 404
        finally:
            cleanup_agent()


# ---------------------------------------------------------------------------
# Multi-turn
# ---------------------------------------------------------------------------


class TestMultiTurn:
    """Verify multi-turn conversations via POST /sessions/{id}/prompt."""

    def test_multi_turn_preserves_state(self, api, create_agent, create_session, runtime):
        """First turn creates a file, second turn reads it back."""
        agent = create_agent(
            name=_unique(f"e2e-multi-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Create a file at /tmp/fairy_e2e_marker.txt with the exact content "
                "'FAIRY_TURN1_DATA'. Only create the file, do not print anything else."
            ),
            timeout=120,
        )

        result, _ = api.run_session(session["id"])
        assert result["status"] == "completed", (
            f"Turn 1 failed with status={result['status']}, exit_code={result.get('exit_code')}"
        )

        resp = api.send_prompt(
            session["id"],
            prompt="Read the file /tmp/fairy_e2e_marker.txt and print its contents to stdout.",
            timeout=120,
        )
        assert resp.status_code == 202

        result, events = api.run_session(session["id"])
        assert result["status"] == "completed", (
            f"Turn 2 failed with status={result['status']}, exit_code={result.get('exit_code')}"
        )

        output = stream_all_output(events)
        assert "FAIRY_TURN1_DATA" in output, (
            f"Turn 1 data not found in turn 2 output. Output: {output[:500]}"
        )

    def test_send_prompt_while_running_rejected(
        self, api, create_agent, create_session, runtime
    ):
        """Cannot send a prompt while the session is already running."""
        agent = create_agent(
            name=_unique(f"e2e-busy-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt=(
                "Count from 1 to 100, printing each number on a new line. "
                "Take your time."
            ),
            timeout=120,
        )

        # Poll briefly for the session to flip to running rather than sleeping
        # blindly. Bail out fast if it completed before we noticed.
        deadline = time.time() + 10
        status = "pending"
        while time.time() < deadline:
            status = api.get_session(session["id"]).json()["status"]
            if status in ("running", "completed", "failed", "terminated"):
                break
            time.sleep(0.1)

        if status == "running":
            resp = api.send_prompt(session["id"], prompt="interrupt")
            assert resp.status_code == 409
