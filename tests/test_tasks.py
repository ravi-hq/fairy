"""Unit tests for `session_service.tasks.execute_turn`.

These invoke the task body as a plain function (Procrastinate tasks are
callable in-process — they only hit the broker when `.defer()` is used).
That lets us cover the per-turn state-machine contract without spinning up
a worker or needing Postgres locally.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from sprites import ExecError

from agent_on_demand.models import (
    AgentSession,
    AgentSessionLog,
    APIKey,
    SessionTurn,
    UserSpritesKey,
)
from agent_on_demand.session_service.tasks import execute_turn


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="testuser", password="testpass")
    APIKey.create_key(u, "test-key")
    usk = UserSpritesKey(user=u)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    return u


def _make_session_and_turn(user):
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="test",
        sprite_name="sprite-abc",
        status="pending",
    )
    turn = SessionTurn.objects.create(
        session=session, turn_number=1, prompt="test", status="pending"
    )
    return session, turn


def _patch_sprite(mocker, exit_behavior):
    """Return (mock_sprite, mock_cmd). `exit_behavior` controls what
    `cmd.run()` does: "success", ExecError, or a generic Exception."""
    mock_cmd = mocker.MagicMock()
    if exit_behavior == "success":
        mock_cmd.run.return_value = None
    elif isinstance(exit_behavior, ExecError):
        mock_cmd.run.side_effect = exit_behavior
    else:
        mock_cmd.run.side_effect = exit_behavior
    mock_sprite = mocker.MagicMock()
    mock_sprite.command.return_value = mock_cmd
    mocker.patch(
        "agent_on_demand.session_service.tasks.resume_session",
        return_value=mock_sprite,
    )
    return mock_sprite, mock_cmd


@pytest.mark.django_db
def test_execute_turn_marks_completed_on_success(user, mocker):
    session, turn = _make_session_and_turn(user)
    _patch_sprite(mocker, "success")

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    session.refresh_from_db()
    turn.refresh_from_db()
    assert session.status == "completed"
    assert session.exit_code == 0
    assert turn.status == "completed"
    assert turn.exit_code == 0
    assert turn.started_at is not None
    assert turn.ended_at is not None


@pytest.mark.django_db
def test_execute_turn_marks_failed_on_exec_error(user, mocker):
    """Regression: ExecError.exit_code is a method, not a property. The
    task body must call it before storing on session.exit_code, otherwise
    Django's IntegerField raises TypeError at save time."""
    session, turn = _make_session_and_turn(user)
    _, mock_cmd = _patch_sprite(mocker, ExecError("exit status 1", exit_code=1))

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hello",
        mode="run",
        timeout=10.0,
    )

    session.refresh_from_db()
    turn.refresh_from_db()
    assert session.status == "failed"
    assert session.exit_code == 1
    assert isinstance(session.exit_code, int)
    assert turn.status == "failed"
    assert turn.exit_code == 1

    # Prompt was fed to the runtime CLI via stdin — not argv, not env, not a file.
    assert mock_cmd.stdin is not None
    assert mock_cmd.stdin.read() == b"hello"


@pytest.mark.django_db
def test_execute_turn_builds_expected_bash_command(user, mocker):
    session, turn = _make_session_and_turn(user)
    mock_sprite, _ = _patch_sprite(mocker, "success")

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hello",
        mode="run",
        timeout=10.0,
    )

    argv = mock_sprite.command.call_args.args
    assert argv[0] == "bash"
    assert argv[1] == "-c"
    assert "/run-agent.sh" not in argv[2]
    assert "source /tmp/aod-env" in argv[2]
    assert "PROMPT=$(cat)" in argv[2]


@pytest.mark.django_db
def test_execute_turn_preserves_terminated_status(user, mocker):
    """Race: a /terminate can flip session.status to 'terminated' while the
    task is mid-flight. When the task finishes it must NOT overwrite that
    with 'completed'/'failed'. The guard is `refresh_from_db + skip save`."""
    session, turn = _make_session_and_turn(user)
    _patch_sprite(mocker, "success")

    # Mock refresh_from_db to simulate DB showing "terminated" after the run.
    def fake_refresh(self, *args, **kwargs):
        if self.pk == session.pk:
            self.status = "terminated"

    mocker.patch.object(AgentSession, "refresh_from_db", fake_refresh)

    # Track session saves by status value.
    original_save = AgentSession.save
    statuses_saved = []

    def tracking_save(self, *args, **kwargs):
        if self.pk == session.pk:
            statuses_saved.append(self.status)
        return original_save(self, *args, **kwargs)

    mocker.patch.object(AgentSession, "save", tracking_save)

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Only the initial → "running" save. The final save was skipped because
    # the refresh guard saw "terminated".
    assert statuses_saved == ["running"]


@pytest.mark.django_db
def test_execute_turn_closes_db_connections(user, mocker):
    """Django's per-request connection lifecycle doesn't apply to Procrastinate
    worker threads. We call close_old_connections() on entry and exit so a
    turn's DB writes don't hold a connection for the worker's full lifetime."""
    session, turn = _make_session_and_turn(user)
    _patch_sprite(mocker, "success")

    close_spy = mocker.patch("agent_on_demand.session_service.tasks.close_old_connections")

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    # At minimum: called at entry and at exit (finally).
    assert close_spy.call_count >= 2


@pytest.mark.django_db
def test_execute_turn_writes_log_chunks_via_tagging_writer(user, mocker):
    """When the SDK pushes stdout chunks through the TaggingQueueWriter, they
    land as `AgentSessionLog` rows tagged with the turn."""
    session, turn = _make_session_and_turn(user)
    _, mock_cmd = _patch_sprite(mocker, "success")

    # Capture the writer that the task attached to cmd.stdout, then replay
    # some chunks through it so the task's drain loop picks them up before
    # the sentinel arrives.
    def drive_output(*_, **__):
        writer = mock_cmd.stdout
        writer.write(b"first chunk\n")
        writer.write(b"second chunk\n")

    mock_cmd.run.side_effect = drive_output

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    logs = list(AgentSessionLog.objects.filter(session=session).order_by("id"))
    assert any("first chunk" in log.data for log in logs)
    assert any("second chunk" in log.data for log in logs)
    assert all(log.turn_id == turn.id for log in logs)
