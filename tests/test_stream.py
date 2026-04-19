"""Unit tests for `stream.stream_session_from_db` and the stream view."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    APIKey,
    AgentSession,
    AgentSessionLog,
    SessionTurn,
)
from agent_on_demand.stream import stream_session_from_db


@pytest.fixture
def user(db):
    return User.objects.create_user(username="streamuser", password="pass")


@pytest.fixture
def api_key(user):
    instance, raw_key = APIKey.create_key(user, "test-key")
    return instance, raw_key


@pytest.fixture
def auth_headers(api_key):
    _, raw_key = api_key
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def completed_session(user):
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session,
        turn_number=1,
        prompt="test",
        status="completed",
    )
    return session, turn


def _seed_logs(session, turn, count=10, start_id_offset=None):
    """Create `count` log rows and return the created objects."""
    logs = []
    for i in range(count):
        log = AgentSessionLog.objects.create(
            session=session,
            turn=turn,
            stream="stdout",
            data=f"chunk-{i + 1}",
        )
        logs.append(log)
    return logs


# ---------------------------------------------------------------------------
# stream_session_from_db — generator tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stream_replays_from_since_cursor(completed_session):
    """since=5 → only events with id > 5 are emitted."""
    session, turn = completed_session
    logs = _seed_logs(session, turn, count=10)
    cutoff = logs[4].id  # 5th row (0-indexed)

    events = list(stream_session_from_db(str(session.id), since=cutoff))

    # Filter to output events only (exclude exit)
    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 5
    emitted_ids = [json.loads(e)["id"] for e in output_events]
    assert all(eid > cutoff for eid in emitted_ids)
    assert min(emitted_ids) == logs[5].id


@pytest.mark.django_db
def test_stream_full_replay_when_since_zero(completed_session):
    """since=0 → all 10 rows replayed."""
    session, turn = completed_session
    logs = _seed_logs(session, turn, count=10)

    events = list(stream_session_from_db(str(session.id), since=0))

    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 10
    emitted_ids = [json.loads(e)["id"] for e in output_events]
    assert emitted_ids[0] == logs[0].id


@pytest.mark.django_db
def test_stream_lenient_on_stale_cursor(completed_session):
    """since < min(log_id) → all rows still emitted (no error, no replay from 0)."""
    session, turn = completed_session
    # Seed 10 rows; their IDs will be > 50 since DB auto-increments
    logs = _seed_logs(session, turn, count=10)
    min_id = logs[0].id
    stale_cursor = max(0, min_id - 100)  # well below range

    events = list(stream_session_from_db(str(session.id), since=stale_cursor))

    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 10


@pytest.mark.django_db
def test_stream_emits_stale_event_on_idle_timeout(user, mocker):
    """When STREAM_IDLE_LIMIT seconds pass with no new chunks, generator emits stale and stops."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="running",
    )

    # Patch the `time` module as imported in `agent_on_demand.stream`
    # so time.time() jumps past STREAM_IDLE_LIMIT immediately.
    mock_time = mocker.patch("agent_on_demand.stream.time")
    mock_time.sleep = lambda x: None

    call_count = 0

    def fast_time():
        nonlocal call_count
        call_count += 1
        # First two calls (last_heartbeat, last_chunk_time init) return 0.
        # Subsequent calls return > STREAM_IDLE_LIMIT to trigger stale.
        if call_count <= 2:
            return 0.0
        return 700.0

    mock_time.time.side_effect = fast_time

    events = list(stream_session_from_db(str(session.id), since=0))

    event_types = [json.loads(e)["type"] for e in events]
    assert "stale" in event_types
    # Generator must terminate after stale
    assert event_types[-1] == "stale"


# ---------------------------------------------------------------------------
# stream view — HTTP tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stream_emits_id_field_via_view(client: Client, auth_headers, user):
    """Wire format must include `id: N` before each non-start data line."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    AgentSessionLog.objects.create(session=session, turn=turn, stream="stdout", data="hello")

    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()

    lines = content.splitlines()
    # Find lines that carry an id: prefix
    id_lines = [line for line in lines if line.startswith("id: ")]
    assert len(id_lines) >= 1, f"No id: lines found in:\n{content}"

    # Each id: line should be followed by a data: line
    for i, line in enumerate(lines):
        if line.startswith("id: "):
            assert i + 1 < len(lines), "id: line at end of stream with no data: following"
            assert lines[i + 1].startswith("data: "), (
                f"id: line not followed by data: line. Got: {lines[i + 1]!r}"
            )


@pytest.mark.django_db
def test_stream_view_parses_last_event_id_header(client: Client, auth_headers, user):
    """HTTP_LAST_EVENT_ID=3 → only rows with id > 3 emitted."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = _seed_logs(session, turn, count=5)
    cutoff = logs[2].id  # after 3rd log

    resp = client.get(
        f"/sessions/{session.id}/stream",
        HTTP_LAST_EVENT_ID=str(cutoff),
        **auth_headers,
    )
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()

    # Parse data lines
    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert all(eid > cutoff for eid in output_ids), (
        f"Expected all output ids > {cutoff}, got {output_ids}"
    )
    assert len(output_ids) == 2


@pytest.mark.django_db
def test_stream_view_parses_since_query_param(client: Client, auth_headers, user):
    """?since=N → only rows with id > N emitted."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = _seed_logs(session, turn, count=5)
    cutoff = logs[2].id

    resp = client.get(
        f"/sessions/{session.id}/stream?since={cutoff}",
        **auth_headers,
    )
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()

    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert len(output_ids) == 2
    assert all(eid > cutoff for eid in output_ids)


@pytest.mark.django_db
def test_stream_view_header_precedence(client: Client, auth_headers, user):
    """When both Last-Event-ID header and ?since= param are present, header wins."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = _seed_logs(session, turn, count=5)
    # Header says skip first 5, param says skip first 2 — header should win
    header_cutoff = logs[4].id  # skip all 5
    param_cutoff = logs[1].id  # would keep 3

    resp = client.get(
        f"/sessions/{session.id}/stream?since={param_cutoff}",
        HTTP_LAST_EVENT_ID=str(header_cutoff),
        **auth_headers,
    )
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()

    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    # Header wins: since=logs[4].id → 0 output events (all 5 skipped)
    assert output_ids == [], f"Expected 0 output events, got ids: {output_ids}"


@pytest.mark.django_db
def test_stream_view_rejects_non_integer_since(client: Client, auth_headers, user):
    """?since=abc → 400 with detail message."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    resp = client.get(f"/sessions/{session.id}/stream?since=abc", **auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "since must be an integer"


@pytest.mark.django_db
def test_stream_view_clamps_negative_since_to_zero(client: Client, auth_headers, user):
    """?since=-5 → treated as 0, full replay."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    _seed_logs(session, turn, count=3)

    resp = client.get(f"/sessions/{session.id}/stream?since=-5", **auth_headers)
    assert resp.status_code == 200
    content = b"".join(resp.streaming_content).decode()

    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert len(output_ids) == 3
