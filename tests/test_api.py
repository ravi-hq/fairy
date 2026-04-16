import json
import uuid

import pytest
from django.test import Client


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
def test_run_invalid_json(client: Client):
    resp = client.post("/sessions", data="not json", content_type="application/json")
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_missing_fields(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude"}),
        content_type="application/json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_invalid_runtime(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "invalid", "prompt": "hello", "api_key": "fake"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "Unknown runtime" in resp.json()["detail"]


@pytest.mark.django_db
def test_run_timeout_too_low(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps({"runtime": "claude", "prompt": "hello", "api_key": "fake", "timeout": 5}),
        content_type="application/json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_timeout_too_high(client: Client):
    resp = client.post(
        "/sessions",
        data=json.dumps(
            {"runtime": "claude", "prompt": "hello", "api_key": "fake", "timeout": 9999}
        ),
        content_type="application/json",
    )
    assert resp.status_code == 422


@pytest.mark.django_db
def test_run_returns_202_with_session_id(client: Client, mocker):
    """POST /run returns 202 with session info (mock Sprites)."""
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
        data=json.dumps({"runtime": "claude", "prompt": "hello", "api_key": "fake-key"}),
        content_type="application/json",
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "id" in data
    assert data["status"] == "pending"
    assert "stream_url" in data
    assert data["stream_url"].startswith("/sessions/")

    # Verify session was created in DB
    from fairy.models import AgentSession

    session = AgentSession.objects.get(pk=data["id"])
    assert session.runtime == "claude"
    assert session.status == "pending"
    assert session.prompt == "hello"


@pytest.mark.django_db
def test_get_session(client: Client):
    from fairy.models import AgentSession

    session = AgentSession.objects.create(
        runtime="claude", prompt="test", status="running"
    )
    resp = client.get(f"/sessions/{session.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(session.id)
    assert data["runtime"] == "claude"
    assert data["status"] == "running"
    assert data["exit_code"] is None


@pytest.mark.django_db
def test_get_session_not_found(client: Client):
    resp = client.get(f"/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_stream_session_replays_completed(client: Client):
    """Stream endpoint replays logs from a completed session."""
    from fairy.models import AgentSession, AgentSessionLog

    session = AgentSession.objects.create(
        runtime="claude", prompt="test", status="completed", exit_code=0
    )
    AgentSessionLog.objects.create(session=session, stream="stdout", data="hello world")
    AgentSessionLog.objects.create(session=session, stream="stderr", data="warning msg")

    resp = client.get(f"/sessions/{session.id}/stream")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/event-stream"

    content = b"".join(resp.streaming_content).decode()
    assert '"type": "start"' in content
    assert "hello world" in content
    assert "warning msg" in content
    assert '"type": "exit"' in content


@pytest.mark.django_db
def test_stream_session_not_found(client: Client):
    resp = client.get(f"/sessions/{uuid.uuid4()}/stream")
    assert resp.status_code == 404


@pytest.mark.django_db
def test_stream_session_failed_with_no_exit_code(client: Client):
    """Failed session with no exit code yields error event."""
    from fairy.models import AgentSession

    session = AgentSession.objects.create(
        runtime="claude", prompt="test", status="failed", exit_code=None
    )

    resp = client.get(f"/sessions/{session.id}/stream")
    content = b"".join(resp.streaming_content).decode()
    assert '"type": "error"' in content
    assert "Session failed" in content
