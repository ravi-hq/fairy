import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import AsyncClient, Client

from agent_on_demand.models import (
    Agent,
    APIKey,
    AgentSession,
    AgentSessionLog,
    SessionTurn,
    UserQuota,
    UserRuntimeKey,
    UserSpritesKey,
)


async def _create_stream_user():
    """Create a user+API key for async streaming tests. Returns (user, raw_key)."""
    import secrets

    user = await User.objects.acreate(
        username=f"streamtest_{secrets.token_hex(4)}",
        password="!unusable",
    )
    raw_key = f"aod_{secrets.token_urlsafe(32)}"
    await APIKey.objects.acreate(
        user=user,
        key_hash=APIKey.hash_key(raw_key),
        key_prefix=raw_key[:12],
        name="test-key",
    )
    return user, raw_key


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def api_key(user):
    """Create an API key and return (APIKey instance, raw key string)."""
    instance, raw_key = APIKey.create_key(user, "test-key")
    return instance, raw_key


@pytest.fixture
def auth_headers(api_key):
    """Return HTTP headers dict for authenticated requests."""
    _, raw_key = api_key
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def sprites_key(user):
    """Create a UserSpritesKey so session creation passes the Sprites-key check."""
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    return usk


@pytest.fixture
def runtime_key(user, sprites_key):
    """Create a UserRuntimeKey for the claude runtime.

    Depends on `sprites_key` so tests that exercise session create/prompt also
    have the per-user Sprites token configured.
    """
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


@pytest.fixture
def runtime_key_without_sprites(user):
    """Runtime key configured, but no UserSpritesKey — for negative tests."""
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user,
        name="Test Agent",
        model="claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )


@pytest.mark.django_db
def test_health(client: Client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_rejects_post(client: Client):
    resp = client.post("/health")
    assert resp.status_code == 405


@pytest.mark.django_db
def test_unauthenticated_request_rejected(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(uuid.uuid4()), "prompt": "hello"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


@pytest.mark.django_db
def test_invalid_api_key_rejected(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(uuid.uuid4()), "prompt": "hello"}),
        content_type="application/json",
        HTTP_AUTHORIZATION="Bearer aod_invalid_key",
    )
    assert resp.status_code == 401


@pytest.mark.django_db
def test_inactive_api_key_rejected(client: Client, api_key):
    instance, raw_key = api_key
    instance.is_active = False
    instance.save()
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(uuid.uuid4()), "prompt": "hello"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert resp.status_code == 401
    assert "inactive" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_invalid_json(client: Client, auth_headers):
    resp = client.post(
        "/sessions", data="not json", content_type="application/json", **auth_headers
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_missing_fields(client: Client, auth_headers):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(uuid.uuid4())}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_agent_not_found(client: Client, auth_headers):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(uuid.uuid4()), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 404
    assert "Agent not found" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_no_runtime_key(client: Client, auth_headers, agent):
    """Authenticated but no UserRuntimeKey configured for the agent's runtime."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "No API key configured" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_no_sprites_key(client: Client, auth_headers, runtime_key_without_sprites, agent):
    """Runtime key is set but no UserSpritesKey — session create must 400."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "No Sprites API key configured" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_timeout_too_low(client: Client, auth_headers, runtime_key, agent):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello", "timeout": 5}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_timeout_too_high(client: Client, auth_headers, runtime_key, agent):
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello", "timeout": 9999}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_returns_202_with_session_id(
    client: Client, auth_headers, runtime_key, agent, fake_sprites
):
    """POST /sessions returns 202 with session info (fake Sprites)."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert "stream_url" in data
    assert data["stream_url"].startswith("/sessions/")

    session = AgentSession.objects.get(pk=data["id"])
    assert session.runtime == "claude"
    assert session.status == "pending"
    assert session.prompt == "hello"
    assert session.user == runtime_key.user
    assert session.agent == agent


@pytest.mark.django_db
def test_run_blocked_at_concurrency_cap(
    client: Client, auth_headers, runtime_key, agent, user, settings
):
    """POST /sessions returns 429 once the user is at their concurrency cap."""
    settings.DEFAULT_MAX_CONCURRENT_SESSIONS = 2
    AgentSession.objects.create(user=user, runtime="claude", prompt="a", status="pending")
    AgentSession.objects.create(user=user, runtime="claude", prompt="b", status="running")

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["limit"] == 2
    assert body["active"] == 2


@pytest.mark.django_db
def test_run_terminal_sessions_do_not_count(
    client: Client, auth_headers, runtime_key, agent, user, fake_sprites, settings
):
    """Completed / failed / terminated sessions don't consume quota."""
    settings.DEFAULT_MAX_CONCURRENT_SESSIONS = 2
    for status in ("completed", "failed", "terminated"):
        AgentSession.objects.create(user=user, runtime="claude", prompt="x", status=status)

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202


@pytest.mark.django_db
def test_run_per_user_quota_override(
    client: Client, auth_headers, runtime_key, agent, user, settings
):
    """A UserQuota row overrides the default cap."""
    settings.DEFAULT_MAX_CONCURRENT_SESSIONS = 10
    UserQuota.objects.create(user=user, max_concurrent_sessions=1)
    AgentSession.objects.create(user=user, runtime="claude", prompt="a", status="running")

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 429
    assert resp.json()["limit"] == 1


@pytest.mark.django_db
def test_run_other_users_sessions_do_not_count(
    client: Client, auth_headers, runtime_key, agent, fake_sprites, settings
):
    """Another user's active sessions don't consume this user's quota."""
    settings.DEFAULT_MAX_CONCURRENT_SESSIONS = 1
    other = User.objects.create_user(username="other", password="pass")
    for _ in range(5):
        AgentSession.objects.create(user=other, runtime="claude", prompt="x", status="running")

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202


@pytest.mark.django_db
def test_get_session(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="running"
    )
    resp = client.get(f"/sessions/{session.id}", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(session.id)
    assert data["runtime"] == "claude"
    assert data["status"] == "running"
    assert data["exit_code"] is None


@pytest.mark.django_db
def test_get_session_not_found(client: Client, auth_headers):
    resp = client.get(f"/sessions/{uuid.uuid4()}", **auth_headers)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_get_session_other_user_not_visible(client: Client, auth_headers):
    """Users cannot see sessions belonging to other users."""
    other_user = User.objects.create_user(username="other", password="pass")
    session = AgentSession.objects.create(
        user=other_user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    resp = client.get(f"/sessions/{session.id}", **auth_headers)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_list_sessions_requires_auth(client: Client):
    resp = client.get("/sessions")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_list_sessions_empty(client: Client, auth_headers):
    resp = client.get("/sessions", **auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"data": []}


@pytest.mark.django_db
def test_list_sessions_returns_user_sessions_newest_first(client: Client, auth_headers, user):
    older = AgentSession.objects.create(
        user=user, runtime="claude", prompt="one", status="completed", exit_code=0
    )
    newer = AgentSession.objects.create(user=user, runtime="claude", prompt="two", status="running")

    resp = client.get("/sessions", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [s["id"] for s in data] == [str(newer.id), str(older.id)]

    first = data[0]
    assert first["runtime"] == "claude"
    assert first["status"] == "running"
    assert first["exit_code"] is None
    assert "created_at" in first and "updated_at" in first
    assert first["resources"] == []
    # prompt is not exposed on session read endpoints
    assert "prompt" not in first


@pytest.mark.django_db
def test_list_sessions_includes_terminal_states(client: Client, auth_headers, user):
    """Listing returns all sessions regardless of status — there's no archive concept."""
    AgentSession.objects.create(user=user, runtime="claude", prompt="p", status="terminated")
    AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", status="failed", exit_code=2
    )

    resp = client.get("/sessions", **auth_headers)
    assert resp.status_code == 200
    statuses = sorted(s["status"] for s in resp.json()["data"])
    assert statuses == ["failed", "terminated"]


@pytest.mark.django_db
def test_list_sessions_scoped_to_user(client: Client, auth_headers, user):
    AgentSession.objects.create(user=user, runtime="claude", prompt="mine", status="completed")
    other = User.objects.create_user(username="other", password="pass")
    AgentSession.objects.create(user=other, runtime="claude", prompt="theirs", status="completed")

    resp = client.get("/sessions", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert AgentSession.objects.get(pk=data[0]["id"]).user == user


@pytest.mark.django_db(transaction=True)
async def test_stream_session_replays_completed(async_client: AsyncClient):
    """Stream endpoint replays logs from a completed session."""
    user, raw_key = await _create_stream_user()
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    await AgentSessionLog.objects.acreate(session=session, stream="stdout", data="hello world")
    await AgentSessionLog.objects.acreate(session=session, stream="stderr", data="warning msg")

    resp = await async_client.get(
        f"/sessions/{session.id}/stream",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"

    content = b"".join([chunk async for chunk in resp.streaming_content]).decode()
    assert '"type": "start"' in content
    assert "hello world" in content
    assert "warning msg" in content
    assert '"type": "exit"' in content


@pytest.mark.django_db
def test_stream_session_not_found(client: Client, auth_headers):
    resp = client.get(f"/sessions/{uuid.uuid4()}/stream", **auth_headers)
    assert resp.status_code == 404


@pytest.mark.django_db(transaction=True)
async def test_stream_session_failed_with_no_exit_code(async_client: AsyncClient):
    """Failed session with no exit code yields error event."""
    user, raw_key = await _create_stream_user()
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="failed", exit_code=None
    )

    resp = await async_client.get(
        f"/sessions/{session.id}/stream",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    content = b"".join([chunk async for chunk in resp.streaming_content]).decode()
    assert '"type": "error"' in content
    assert "Session failed" in content


@pytest.mark.django_db
def test_terminate_session(client: Client, auth_headers, sprites_key, user, fake_sprites):
    """Terminate flips the DB state and enqueues the Sprite delete.

    The `fake_sprites` fixture runs `destroy_session_task.defer` inline, so
    we can still assert on the recorded delete — but the view itself returns
    without blocking on the Sprites API call.
    """
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", sprite_name="aod-abc123", status="completed"
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "terminated"

    session.refresh_from_db()
    assert session.status == "terminated"
    assert session.sprite_name == ""

    assert fake_sprites.deleted == ["aod-abc123"]


@pytest.mark.django_db
def test_terminate_already_terminated(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", sprite_name="", status="terminated"
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 409
    assert "already terminated" in resp.json()["detail"]


@pytest.mark.django_db
def test_create_session_creates_turn_one(
    client: Client, auth_headers, runtime_key, agent, fake_sprites
):
    """POST /sessions produces exactly one turn with prompt + pending status."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "first prompt"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["current_turn"] == 1

    turns = list(SessionTurn.objects.filter(session_id=data["id"]).order_by("turn_number"))
    assert len(turns) == 1
    assert turns[0].turn_number == 1
    assert turns[0].prompt == "first prompt"
    assert turns[0].status == "pending"


@pytest.mark.django_db
def test_create_session_generates_runtime_session_id(
    client: Client, auth_headers, runtime_key, agent, fake_sprites
):
    """Each new session gets a pre-generated UUID persisted as
    runtime_session_id AND written into the /tmp/aod-env file as AOD_SESSION_ID."""
    import uuid as _uuid

    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hi"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    session = AgentSession.objects.get(pk=resp.json()["id"])
    assert session.runtime_session_id is not None
    _uuid.UUID(str(session.runtime_session_id), version=4)

    env_file = fake_sprites.last_sprite().write_map()["/tmp/aod-env"]
    assert f"AOD_SESSION_ID={session.runtime_session_id}" in env_file


@pytest.mark.django_db
def test_create_session_writes_env_file_only(
    client: Client, auth_headers, runtime_key, agent, fake_sprites
):
    """Provisioning writes the env file. There is no dispatcher script and no
    prompt file — the runtime CLI is assembled inline per turn and the prompt
    streams over stdin."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    writes = fake_sprites.last_sprite().write_map()
    assert "/tmp/aod-env" in writes
    assert "/run-agent.sh" not in writes
    assert "/tmp/aod-prompt.txt" not in writes


@pytest.mark.django_db
def test_send_prompt_appends_turn(
    client: Client, auth_headers, runtime_key, agent, user, fake_sprites
):
    """/prompt creates turn N+1 and writes only the prompt file."""
    session = AgentSession.objects.create(
        user=user,
        agent=agent,
        runtime="claude",
        prompt="first",
        sprite_name="sprite-xyz",
        status="completed",
    )
    SessionTurn.objects.create(
        session=session, turn_number=1, prompt="first", status="completed", exit_code=0
    )

    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "second"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    assert resp.json()["current_turn"] == 2

    turns = list(SessionTurn.objects.filter(session=session).order_by("turn_number"))
    assert [t.turn_number for t in turns] == [1, 2]
    assert turns[1].prompt == "second"
    assert turns[1].status == "pending"

    # /prompt is fire-and-forget: no filesystem writes on the Sprite — the
    # prompt streams over stdin once the background thread starts.
    sprite = fake_sprites.sprites["sprite-xyz"]
    assert sprite.writes == []


@pytest.mark.django_db
def test_send_prompt_enqueues_continue_task(
    client: Client, auth_headers, runtime_key, agent, user, mocker
):
    """/prompt enqueues an `execute_turn` task with mode="continue"."""
    from tests.fakes.sprite import RecordingSpritesClient

    session = AgentSession.objects.create(
        user=user,
        agent=agent,
        runtime="claude",
        prompt="first",
        sprite_name="sprite-xyz",
        status="completed",
    )
    SessionTurn.objects.create(
        session=session, turn_number=1, prompt="first", status="completed", exit_code=0
    )

    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)

    defer_mock = mocker.patch("agent_on_demand.session_service.turn.execute_turn.defer")

    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "second"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    # execute_turn.defer is keyword-only.
    kwargs = defer_mock.call_args.kwargs
    assert kwargs["mode"] == "continue"
    assert kwargs["prompt"] == "second"
    assert kwargs["session_id"] == str(session.id)
    assert isinstance(kwargs["turn_id"], int)


@pytest.mark.django_db
def test_list_session_turns(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t2", status="completed", exit_code=0
    )
    SessionTurn.objects.create(
        session=session, turn_number=1, prompt="t1", status="completed", exit_code=0
    )
    SessionTurn.objects.create(
        session=session, turn_number=2, prompt="t2", status="completed", exit_code=0
    )

    resp = client.get(f"/sessions/{session.id}/turns", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [t["turn_number"] for t in data] == [1, 2]
    assert data[0]["prompt"] == "t1"
    assert data[1]["prompt"] == "t2"


@pytest.mark.django_db
def test_session_read_exposes_turn_count(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t2", status="completed", exit_code=0
    )
    SessionTurn.objects.create(session=session, turn_number=1, prompt="t1", status="completed")
    SessionTurn.objects.create(session=session, turn_number=2, prompt="t2", status="completed")

    resp = client.get(f"/sessions/{session.id}", **auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["turn_count"] == 2
    assert body["current_turn"] == 2


@pytest.mark.django_db(transaction=True)
async def test_stream_emits_turn_start_boundaries(async_client: AsyncClient):
    """Logs are tagged with their turn; stream emits a turn_start event when
    the turn boundary changes."""
    user, raw_key = await _create_stream_user()
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="t2", status="completed", exit_code=0
    )
    t1 = await SessionTurn.objects.acreate(
        session=session, turn_number=1, prompt="t1", status="completed", exit_code=0
    )
    t2 = await SessionTurn.objects.acreate(
        session=session, turn_number=2, prompt="t2", status="completed", exit_code=0
    )
    await AgentSessionLog.objects.acreate(session=session, turn=t1, stream="stdout", data="a")
    await AgentSessionLog.objects.acreate(session=session, turn=t2, stream="stdout", data="b")

    resp = await async_client.get(
        f"/sessions/{session.id}/stream",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    content = b"".join([chunk async for chunk in resp.streaming_content]).decode()
    # Two turn_start events in order: 1 then 2.
    turn_start_events = [
        json.loads(line[6:])
        for line in content.splitlines()
        if line.startswith("data: ")
        and json.loads(line[6:]).get("type") == "turn_start"
    ]
    turns = [e["turn"] for e in turn_start_events]
    assert turns == [1, 2]


@pytest.mark.django_db
def test_send_prompt_to_terminated_session(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", sprite_name="", status="terminated"
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "terminated" in resp.json()["detail"]


@pytest.mark.django_db
def test_send_prompt_to_failed_session_rejected(client: Client, auth_headers, user):
    """Failed sessions are terminal: the underlying Sprite may still be running
    (see Muddy Zone 8). Resuming would risk two concurrent executions on the
    same Sprite, so we force callers to start a new session instead."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        sprite_name="sprite-xyz",
        status="failed",
        exit_code=1,
    )
    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "retry"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 409
    assert "failed" in resp.json()["detail"].lower()


@pytest.mark.django_db(transaction=True)
async def test_stream_terminated_session(async_client: AsyncClient):
    """Stream endpoint yields terminated event for terminated sessions."""
    user, raw_key = await _create_stream_user()
    session = await AgentSession.objects.acreate(
        user=user, runtime="claude", prompt="test", status="terminated"
    )
    resp = await async_client.get(
        f"/sessions/{session.id}/stream",
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    content = b"".join([chunk async for chunk in resp.streaming_content]).decode()
    assert '"type": "terminated"' in content
