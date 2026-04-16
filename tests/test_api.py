import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import Agent, APIKey, AgentSession, AgentSessionLog, UserRuntimeKey


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
def runtime_key(user):
    """Create a UserRuntimeKey for the claude runtime."""
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


@pytest.fixture
def agent(user):
    return Agent.objects.create(
        user=user, name="Test Agent", model="claude-sonnet-4-6",
        runtime="claude", version=1,
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
        HTTP_AUTHORIZATION="Bearer fairy_invalid_key",
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
    resp = client.post("/sessions", data="not json", content_type="application/json", **auth_headers)
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
def test_run_returns_202_with_session_id(client: Client, auth_headers, runtime_key, agent, mocker):
    """POST /sessions returns 202 with session info (mock Sprites)."""
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()

    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("fairy.views._get_client", return_value=mock_client)

    # Prevent the background thread from actually running
    mocker.patch("fairy.views.threading.Thread")

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

    # Verify session was created in DB with correct user and agent
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
    mock_client = mocker.MagicMock()
    mocker.patch("fairy.views._get_client", return_value=mock_client)

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", sprite_name="fairy-abc123", status="completed"
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "terminated"

    session.refresh_from_db()
    assert session.status == "terminated"
    assert session.sprite_name == ""

    mock_client.delete_sprite.assert_called_once_with("fairy-abc123")


@pytest.mark.django_db
def test_terminate_already_terminated(client: Client, auth_headers, user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", sprite_name="", status="terminated"
    )
    resp = client.post(f"/sessions/{session.id}/terminate", **auth_headers)
    assert resp.status_code == 409
    assert "already terminated" in resp.json()["detail"]


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

    from fairy.stream import run_session_background

    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="pending"
    )

    mock_sprite = mocker.MagicMock()
    mock_cmd = mocker.MagicMock()
    mock_cmd.run.side_effect = ExecError("exit status 1", exit_code=1)
    mock_sprite.command.return_value = mock_cmd

    run_session_background(session, mock_sprite, timeout=10.0)

    session.refresh_from_db()
    assert session.status == "failed"
    assert session.exit_code == 1
    assert isinstance(session.exit_code, int)


@pytest.mark.django_db
def test_stream_terminated_session(client: Client, auth_headers, user):
    """Stream endpoint yields terminated event for terminated sessions."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="test", status="terminated"
    )
    resp = client.get(f"/sessions/{session.id}/stream", **auth_headers)
    content = b"".join(resp.streaming_content).decode()
    assert '"type": "terminated"' in content
