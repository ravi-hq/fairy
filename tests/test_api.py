import json
import uuid

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import APIKey, AgentSession, AgentSessionLog, UserRuntimeKey


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
        data=json.dumps({"runtime": "claude", "prompt": "hello"}),
        content_type="application/json",
    )
    assert resp.status_code == 401


@pytest.mark.django_db
def test_invalid_api_key_rejected(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude", "prompt": "hello"}),
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
        data=json.dumps({"runtime": "claude", "prompt": "hello"}),
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
        data=json.dumps({"runtime": "claude"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_invalid_runtime(client: Client, auth_headers):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "invalid", "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "Unknown runtime" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_no_runtime_key(client: Client, auth_headers):
    """Authenticated but no UserRuntimeKey configured for the runtime."""
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude", "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 400
    assert "No API key configured" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_timeout_too_low(client: Client, auth_headers, runtime_key):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude", "prompt": "hello", "timeout": 5}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_timeout_too_high(client: Client, auth_headers, runtime_key):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude", "prompt": "hello", "timeout": 9999}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_returns_202_with_session_id(client: Client, auth_headers, runtime_key, mocker):
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
        data=json.dumps({"runtime": "claude", "prompt": "hello"}),
        content_type="application/json",
        **auth_headers,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert "stream_url" in data
    assert data["stream_url"].startswith("/sessions/")

    # Verify session was created in DB with correct user
    session = AgentSession.objects.get(pk=data["id"])
    assert session.runtime == "claude"
    assert session.status == "pending"
    assert session.prompt == "hello"
    assert session.user == runtime_key.user


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
