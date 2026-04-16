"""E2E tests for session lifecycle, streaming, termination, and multi-turn."""

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique, stream_all_output


# ---------------------------------------------------------------------------
# Runtime parametrization
# ---------------------------------------------------------------------------


def _runtime_ids(runtimes):
    return runtimes


@pytest.fixture(params=list(RUNTIME_MODELS.keys()))
def runtime(request, e2e_runtimes):
    """Yield each runtime, skipping those not in E2E_RUNTIMES."""
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


# ---------------------------------------------------------------------------
# Session lifecycle (per runtime)
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    """Create a session for each runtime and verify it completes."""

    def test_session_completes(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Print exactly 'FAIRY_E2E_OK' to stdout. Do not create any files.",
            timeout=120,
        )
        assert session["status"] == "pending"
        assert "id" in session

        result = api.wait_for_session(session["id"])
        assert result["status"] == "completed"
        assert result["exit_code"] == 0

    def test_session_has_correct_runtime(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-rt-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say hello.",
            timeout=120,
        )
        result = api.wait_for_session(session["id"])
        assert result["runtime"] == runtime

    def test_get_session_returns_metadata(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-meta-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say hello.",
            timeout=120,
        )
        resp = api.get_session(session["id"])
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session["id"]
        assert data["agent_id"] == agent["id"]
        assert data["runtime"] == runtime
        assert "created_at" in data
        assert "updated_at" in data


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


class TestStreaming:
    """Verify SSE stream events."""

    def test_stream_has_start_and_exit(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-stream-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Print 'hello' and exit immediately.",
            timeout=120,
        )
        events = api.collect_stream(session["id"])

        types = [e["type"] for e in events]
        assert "start" in types, f"Missing 'start' event. Got: {types}"
        assert "exit" in types, f"Missing 'exit' event. Got: {types}"

    def test_start_event_fields(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-start-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        events = api.collect_stream(session["id"])
        start = next(e for e in events if e["type"] == "start")
        assert start["runtime"] == runtime
        assert start["session_id"] == session["id"]

    def test_exit_event_code(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-exit-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Print 'done'.",
            timeout=120,
        )
        events = api.collect_stream(session["id"])
        exit_evt = next(e for e in events if e["type"] == "exit")
        assert exit_evt["code"] == 0

    def test_stream_contains_output(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-output-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Print 'hello world'.",
            timeout=120,
        )
        events = api.collect_stream(session["id"])
        output_events = [e for e in events if e.get("type") == "output"]
        assert len(output_events) > 0, "No output events in stream"

    def test_stream_replay_after_completion(self, api, create_agent, create_session, runtime):
        """Stream a session that has already completed (replay mode)."""
        agent = create_agent(
            name=_unique(f"e2e-replay-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Print 'replay_test'.",
            timeout=120,
        )
        # Wait for completion first
        api.wait_for_session(session["id"])

        # Then stream — should replay all output
        events = api.collect_stream(session["id"])
        types = [e["type"] for e in events]
        assert "start" in types
        assert "exit" in types
        assert any(e["type"] == "output" for e in events)


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------


class TestTermination:
    """Verify session termination."""

    def test_terminate_completed_session(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-term-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        api.wait_for_session(session["id"])

        resp = api.terminate_session(session["id"])
        assert resp.status_code == 200
        assert resp.json()["status"] == "terminated"

    def test_terminate_idempotent_409(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-term2-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        api.wait_for_session(session["id"])
        api.terminate_session(session["id"])

        # Second terminate should 409
        resp = api.terminate_session(session["id"])
        assert resp.status_code == 409

    def test_stream_terminated_session(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-termstream-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        api.wait_for_session(session["id"])
        api.terminate_session(session["id"])

        events = api.collect_stream(session["id"])
        types = [e["type"] for e in events]
        assert "terminated" in types

    def test_send_prompt_to_terminated_session_rejected(
        self, api, create_agent, create_session, runtime
    ):
        agent = create_agent(
            name=_unique(f"e2e-termprompt-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        api.wait_for_session(session["id"])
        api.terminate_session(session["id"])

        resp = api.send_prompt(session["id"], prompt="follow up")
        assert resp.status_code == 409

    def test_delete_completed_session(self, api, create_agent, create_session, runtime):
        agent = create_agent(
            name=_unique(f"e2e-del-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say ok.",
            timeout=120,
        )
        api.wait_for_session(session["id"])

        resp = api.delete_session(session["id"])
        assert resp.status_code == 200

        # Session should be gone
        resp = api.get_session(session["id"])
        assert resp.status_code == 404


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

        # Wait for first turn to complete
        result = api.wait_for_session(session["id"])
        assert result["status"] == "completed", (
            f"Turn 1 failed with status={result['status']}, exit_code={result.get('exit_code')}"
        )

        # Send follow-up prompt
        resp = api.send_prompt(
            session["id"],
            prompt=(
                "Read the file /tmp/fairy_e2e_marker.txt and print its contents to stdout."
            ),
            timeout=120,
        )
        assert resp.status_code == 202

        # Wait for second turn
        result = api.wait_for_session(session["id"])
        assert result["status"] == "completed", (
            f"Turn 2 failed with status={result['status']}, exit_code={result.get('exit_code')}"
        )

        # Verify the output contains the data from turn 1
        events = api.collect_stream(session["id"])
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

        # Give the session a moment to start running
        import time
        time.sleep(3)

        # Verify session is running
        status_resp = api.get_session(session["id"])
        status = status_resp.json()["status"]
        if status == "running":
            resp = api.send_prompt(session["id"], prompt="interrupt")
            assert resp.status_code == 409
