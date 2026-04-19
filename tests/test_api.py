import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import (
    Agent,
    APIKey,
    AgentSession,
    AgentSessionLog,
    SessionTurn,
    UserRuntimeKey,
    UserSpritesKey,
)


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


@pytest.mark.django_db
def test_stream_session_replays_completed(client: Client, auth_headers, user):
    """Stream endpoint replays logs from a completed session."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="completed", exit_code=0
    )
    AgentSessionLog.objects.create(session=session, stream="stdout", data="hello world")
    AgentSessionLog.objects.create(session=session, stream="stderr", data="warning msg")

    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"

    content = b"".join(resp.streaming_content).decode()
    assert '"type": "start"' in content
    assert "hello world" in content
    assert "warning msg" in content
    assert '"type": "exit"' in content


@pytest.mark.django_db
def test_stream_session_not_found(client: Client, auth_headers):
    resp = client.get(f"/sessions/{uuid.uuid4()}/stream", **auth_headers)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_stream_session_failed_with_no_exit_code(client: Client, auth_headers, user):
    """Failed session with no exit code yields error event."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="failed", exit_code=None
    )

    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    content = b"".join(resp.streaming_content).decode()
    assert '"type": "error"' in content
    assert "Session failed" in content


@pytest.mark.django_db
def test_terminate_session(client: Client, auth_headers, user, mocker):
    """Terminate destroys the Sprite but keeps the session record."""
    from tests.fakes.sprite import RecordingSpritesClient

    fake = RecordingSpritesClient()
    mocker.patch("agent_on_demand.session_service.client.get_client", return_value=fake)
    mocker.patch("agent_on_demand.session_service.get_client", return_value=fake)

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

    assert fake.deleted == ["aod-abc123"]


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
def test_send_prompt_invokes_continue_mode(
    client: Client, auth_headers, runtime_key, agent, user, mocker
):
    """/prompt invokes `bash /run-agent.sh continue` on the Sprite."""
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

    # Capture the run_session_background invocation without running it.
    captured = {}

    def fake_thread(target, args, daemon):
        captured["args"] = args
        return mocker.MagicMock()

    mocker.patch("agent_on_demand.session_service.turn.threading.Thread", side_effect=fake_thread)

    resp = client.post(
        f"/sessions/{session.id}/prompt",
        data=json.dumps({"prompt": "second"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202

    # run_session_background(session, turn, sprite, runtime, prompt, mode, timeout)
    _session, _turn, _sprite, _runtime, prompt, mode, _timeout = captured["args"]
    assert mode == "continue"
    assert prompt == "second"
    assert _turn.turn_number == 2


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


@pytest.mark.django_db
def test_stream_emits_turn_start_boundaries(client: Client, auth_headers, user):
    """Logs are tagged with their turn; stream emits a turn_start event when
    the turn boundary changes."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t2", status="completed", exit_code=0
    )
    t1 = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="t1", status="completed", exit_code=0
    )
    t2 = SessionTurn.objects.create(
        session=session, turn_number=2, prompt="t2", status="completed", exit_code=0
    )
    AgentSessionLog.objects.create(session=session, turn=t1, stream="stdout", data="a")
    AgentSessionLog.objects.create(session=session, turn=t2, stream="stdout", data="b")

    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    content = b"".join(resp.streaming_content).decode()
    # Two turn_start events in order: 1 then 2.
    i1 = content.index('"type": "turn_start", "turn": 1')
    i2 = content.index('"type": "turn_start", "turn": 2')
    assert i1 < i2


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
def test_run_session_background_persists_int_exit_code_on_exec_error(user, mocker):
    """Regression: ExecError.exit_code is a method, not a property. The
    background runner must call it before storing on `session.exit_code`,
    otherwise Django's IntegerField raises TypeError at save time."""
    from sprites import ExecError

    from agent_on_demand.models import SessionTurn
    from agent_on_demand.runtimes import RUNTIMES
    from agent_on_demand.stream import run_session_background

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="pending"
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="pending"
    )

    mock_sprite = mocker.MagicMock()
    mock_cmd = mocker.MagicMock()
    mock_cmd.run.side_effect = ExecError("exit status 1", exit_code=1)
    mock_sprite.command.return_value = mock_cmd

    run_session_background(
        session, turn, mock_sprite, RUNTIMES["claude"], "hello", "run", timeout=10.0
    )

    session.refresh_from_db()
    assert session.status == "failed"
    assert session.exit_code == 1
    assert isinstance(session.exit_code, int)

    # Prompt was fed to the runtime CLI via stdin — not argv, not env, not a file.
    assert mock_cmd.stdin is not None
    assert mock_cmd.stdin.read() == b"hello"

    # The inline bash -c command — no /run-agent.sh, no prompt file.
    argv = mock_sprite.command.call_args.args
    assert argv[0] == "bash"
    assert argv[1] == "-c"
    assert "/run-agent.sh" not in argv[2]
    assert "source /tmp/aod-env" in argv[2]
    assert "PROMPT=$(cat)" in argv[2]

    turn.refresh_from_db()
    assert turn.status == "failed"
    assert turn.exit_code == 1
    assert turn.ended_at is not None


@pytest.mark.django_db
def test_stream_terminated_session(client: Client, auth_headers, user):
    """Stream endpoint yields terminated event for terminated sessions."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="terminated"
    )
    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    content = b"".join(resp.streaming_content).decode()
    assert '"type": "terminated"' in content
