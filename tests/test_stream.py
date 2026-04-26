"""Unit tests for `stream.stream_session_from_db` and the stream view."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from django.contrib.auth.models import User
from django.test import AsyncClient, Client

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


async def _create_user_and_headers():
    """Create a user+API key and return (user, async_headers dict) using async ORM.

    Returns headers in ASGI format for AsyncClient (not WSGI HTTP_* format).
    """
    import secrets

    user = await User.objects.acreate(
        username=f"asyncuser_{secrets.token_hex(4)}",
        password="!unusable",
    )
    raw_key = f"aod_{secrets.token_urlsafe(32)}"
    await APIKey.objects.acreate(
        user=user,
        key_hash=APIKey.hash_key(raw_key),
        key_prefix=raw_key[:12],
        name="test-key",
    )
    # AsyncClient uses headers= kwarg (ASGI format), not HTTP_* WSGI format
    return user, {"headers": {"Authorization": f"Bearer {raw_key}"}}


def _seed_logs(session, turn, count=10, start_id_offset=None):
    """Create `count` log rows and return the created objects (sync)."""
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


async def _seed_logs_async(session, turn, count=10):
    """Create `count` log rows and return the created objects (async)."""
    logs = []
    for i in range(count):
        log = await AgentSessionLog.objects.acreate(
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


@pytest.mark.django_db(transaction=True)
async def test_stream_replays_from_since_cursor(user):
    """since=5 → only events with id > 5 are emitted."""
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = await _seed_logs_async(session, turn, count=10)
    cutoff = logs[4].id  # 5th row (0-indexed)

    events = [e async for e in stream_session_from_db(str(session.id), since=cutoff)]

    # Filter to output events only (exclude exit)
    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 5
    emitted_ids = [json.loads(e)["id"] for e in output_events]
    assert all(eid > cutoff for eid in emitted_ids)
    assert min(emitted_ids) == logs[5].id


@pytest.mark.django_db(transaction=True)
async def test_stream_full_replay_when_since_zero(user):
    """since=0 → all 10 rows replayed."""
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = await _seed_logs_async(session, turn, count=10)

    events = [e async for e in stream_session_from_db(str(session.id), since=0)]

    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 10
    emitted_ids = [json.loads(e)["id"] for e in output_events]
    assert emitted_ids[0] == logs[0].id


@pytest.mark.django_db(transaction=True)
async def test_stream_lenient_on_stale_cursor(user):
    """since < min(log_id) → all rows still emitted (no error, no replay from 0)."""
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    # Seed 10 rows; their IDs will be > 50 since DB auto-increments
    logs = await _seed_logs_async(session, turn, count=10)
    min_id = logs[0].id
    stale_cursor = max(0, min_id - 100)  # well below range

    events = [e async for e in stream_session_from_db(str(session.id), since=stale_cursor)]

    output_events = [e for e in events if json.loads(e).get("type") == "output"]
    assert len(output_events) == 10


@pytest.mark.django_db(transaction=True)
async def test_stream_emits_stale_event_on_idle_timeout(user, mocker):
    """When STREAM_IDLE_LIMIT seconds pass with no new chunks, generator emits stale and stops."""
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="running",
    )

    mocker.patch("agent_on_demand.stream.asyncio.sleep", new=AsyncMock(return_value=None))

    # Patch the `time` module as imported in `agent_on_demand.stream`
    # so time.monotonic() jumps past STREAM_IDLE_LIMIT immediately.
    mock_time = mocker.patch("agent_on_demand.stream.time")

    call_count = 0

    def fast_monotonic():
        nonlocal call_count
        call_count += 1
        # First two calls (last_heartbeat, last_chunk_time init) return 0.
        # Subsequent calls return > STREAM_IDLE_LIMIT to trigger stale.
        if call_count <= 2:
            return 0.0
        return 700.0

    mock_time.monotonic.side_effect = fast_monotonic

    events = [e async for e in stream_session_from_db(str(session.id), since=0)]

    event_types = [json.loads(e)["type"] for e in events]
    assert "stale" in event_types
    # Generator must terminate after stale
    assert event_types[-1] == "stale"


# ---------------------------------------------------------------------------
# stream view — HTTP tests
# ---------------------------------------------------------------------------


async def _read_streaming(resp) -> str:
    """Consume an async streaming response and return the decoded content."""
    chunks = []
    async for chunk in resp.streaming_content:
        chunks.append(chunk)
    return b"".join(chunks).decode()


@pytest.mark.django_db(transaction=True)
async def test_stream_yields_stage_events_and_preserves_order(user):
    """Stage rows translate to `stage` SSE events and interleave with output
    rows in id order. Stage rows must not trigger `turn_start`."""
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    # Interleave: two stage rows before the turn starts, then output for turn 1.
    await AgentSessionLog.objects.acreate(
        session=session, kind="stage", stage="create_sprite", state="started"
    )
    await AgentSessionLog.objects.acreate(
        session=session,
        kind="stage",
        stage="create_sprite",
        state="done",
        duration_ms=12000,
    )
    await AgentSessionLog.objects.acreate(
        session=session,
        kind="stage",
        stage="env_file",
        state="failed",
        duration_ms=500,
        data="write failed: permission denied",
    )
    await AgentSessionLog.objects.acreate(session=session, turn=turn, stream="stdout", data="hi")

    events = [json.loads(e) async for e in stream_session_from_db(str(session.id), since=0) if e]
    # stage events first (3), then turn_start, then output, then exit.
    types = [e["type"] for e in events]
    assert types == ["stage", "stage", "stage", "turn_start", "output", "exit"]

    assert events[0] == {
        "type": "stage",
        "id": events[0]["id"],
        "stage": "create_sprite",
        "state": "started",
    }
    assert events[1]["state"] == "done"
    assert events[1]["duration_ms"] == 12000
    assert events[2]["state"] == "failed"
    assert events[2]["duration_ms"] == 500
    assert events[2]["message"] == "write failed: permission denied"


@pytest.mark.django_db(transaction=True)
async def test_stream_emits_id_field_via_view(async_client: AsyncClient):
    """Wire format must include `id: N` before each non-start data line."""
    user, headers = await _create_user_and_headers()
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    await AgentSessionLog.objects.acreate(session=session, turn=turn, stream="stdout", data="hello")

    resp = await async_client.get(f"/sessions/{session.id}/stream", **headers)
    assert resp.status_code == 200
    content = await _read_streaming(resp)

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


@pytest.mark.django_db(transaction=True)
async def test_stream_view_parses_last_event_id_header(async_client: AsyncClient):
    """Last-Event-ID header → only rows with id > header value emitted."""
    user, headers = await _create_user_and_headers()
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = await _seed_logs_async(session, turn, count=5)
    cutoff = logs[2].id  # after 3rd log

    # Merge Last-Event-ID into the headers dict for AsyncClient
    merged_headers = {**headers["headers"], "Last-Event-Id": str(cutoff)}
    resp = await async_client.get(
        f"/sessions/{session.id}/stream",
        headers=merged_headers,
    )
    assert resp.status_code == 200
    content = await _read_streaming(resp)

    # Parse data lines
    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert all(eid > cutoff for eid in output_ids), (
        f"Expected all output ids > {cutoff}, got {output_ids}"
    )
    assert len(output_ids) == 2


@pytest.mark.django_db(transaction=True)
async def test_stream_view_parses_since_query_param(async_client: AsyncClient):
    """?since=N → only rows with id > N emitted."""
    user, headers = await _create_user_and_headers()
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = await _seed_logs_async(session, turn, count=5)
    cutoff = logs[2].id

    resp = await async_client.get(
        f"/sessions/{session.id}/stream?since={cutoff}",
        **headers,
    )
    assert resp.status_code == 200
    content = await _read_streaming(resp)

    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert len(output_ids) == 2
    assert all(eid > cutoff for eid in output_ids)


@pytest.mark.django_db(transaction=True)
async def test_stream_view_header_precedence(async_client: AsyncClient):
    """When both Last-Event-ID header and ?since= param are present, header wins."""
    user, headers = await _create_user_and_headers()
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    logs = await _seed_logs_async(session, turn, count=5)
    # Header says skip first 5, param says skip first 2 — header should win
    header_cutoff = logs[4].id  # skip all 5
    param_cutoff = logs[1].id  # would keep 3

    merged_headers = {**headers["headers"], "Last-Event-Id": str(header_cutoff)}
    resp = await async_client.get(
        f"/sessions/{session.id}/stream?since={param_cutoff}",
        headers=merged_headers,
    )
    assert resp.status_code == 200
    content = await _read_streaming(resp)

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


@pytest.mark.django_db(transaction=True)
async def test_stream_view_clamps_negative_since_to_zero(async_client: AsyncClient):
    """?since=-5 → treated as 0, full replay."""
    user, headers = await _create_user_and_headers()
    session = await AgentSession.objects.acreate(
        user=user,
        runtime="claude",
        prompt="test",
        status="completed",
        exit_code=0,
    )
    turn = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="test", status="completed"
    )
    await _seed_logs_async(session, turn, count=3)

    resp = await async_client.get(f"/sessions/{session.id}/stream?since=-5", **headers)
    assert resp.status_code == 200
    content = await _read_streaming(resp)

    data_payloads = [
        json.loads(line[6:]) for line in content.splitlines() if line.startswith("data: ")
    ]
    output_ids = [p["id"] for p in data_payloads if p.get("type") == "output"]
    assert len(output_ids) == 3


@pytest.mark.django_db(transaction=True)
async def test_stream_emits_heartbeat_after_15s_idle(user, mocker):
    """The generator yields an empty string (SSE comment frame) every 15
    seconds of clock time when there are no new chunks. Without this,
    intermediate proxies (Cloudflare, Render) would close the SSE
    connection after their idle timeout and clients would silently
    reconnect, missing events emitted in the gap.

    Mocks `time.monotonic()` to make the heartbeat trigger fire on the
    second loop iteration without actually sleeping 15 seconds in tests."""
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="x", status="running"
    )

    # Sequence of values returned by time.monotonic on each call:
    # 1: init last_heartbeat
    # 2: init last_chunk_time
    # 3: first loop's `now =` — 15s after init triggers the heartbeat
    # We then flip the session to terminated so the generator exits.
    monotonic_values = iter([0.0, 0.0, 15.0, 15.5])

    def fake_monotonic():
        try:
            return next(monotonic_values)
        except StopIteration:
            return 100.0

    mocker.patch("agent_on_demand.stream.time.monotonic", side_effect=fake_monotonic)

    # Use asyncio.sleep that yields immediately so we can step through the loop.
    async def fake_sleep(_d):
        # Mark session terminated after the first heartbeat so the next loop
        # iteration exits cleanly.
        await AgentSession.objects.filter(pk=session.pk).aupdate(status="terminated")

    mocker.patch("agent_on_demand.stream.asyncio.sleep", side_effect=fake_sleep)

    events = [event async for event in stream_session_from_db(str(session.id))]
    # First non-terminal yield is the heartbeat (empty string), then the
    # terminated-status event closes out.
    assert "" in events, f"expected heartbeat empty-string yield, got {events!r}"
