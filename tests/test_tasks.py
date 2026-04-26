"""Unit tests for `session_service.tasks`.

These invoke task bodies as plain functions (Procrastinate tasks are callable
in-process — they only hit the broker when `.defer()` is used). That lets us
cover the per-turn state-machine contract and the provision-then-enqueue
contract without spinning up a worker or needing Postgres locally.
"""

from __future__ import annotations

import queue
import threading

import pytest
from django.contrib.auth.models import User
from sprites import ExecError, SpriteError

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentSessionLog,
    APIKey,
    SessionTurn,
    UserCredential,
    UserSpritesKey,
)
from agent_on_demand.session_service.tasks import (
    TaggingQueueWriter,
    destroy_session_task,
    execute_turn,
    provision_session_task,
)


@pytest.fixture(autouse=True)
def mock_close_old_connections(mocker):
    """Prevent close_old_connections() from closing the test DB connection.

    Tasks call close_old_connections() in entry/exit wrappers and inside
    _flush_buffer's retry loop. In production that's correct; in tests the
    call kills the pytest-django test transaction, breaking every subsequent
    DB query in the same test. Stub it out so test isolation is preserved.
    """
    mocker.patch("agent_on_demand.session_service.tasks.close_old_connections")


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
def test_execute_turn_builds_expected_argv(user, mocker):
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
    # Thin shim: bash -lc sourcing /tmp/aod-env, then exec "$@" ... argv.
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    assert "source /tmp/aod-env" in argv[2]
    assert 'exec "$@"' in argv[2]
    assert argv[3] == "--"
    # First runtime-CLI token must be the claude binary for this session.
    assert argv[4] == "claude"
    # No shell template substitutions made it into argv.
    assert "/run-agent.sh" not in " ".join(argv)
    assert "PROMPT=$(cat)" not in " ".join(argv)


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

    # Scope to output rows: stage rows (e.g. runtime_start) have turn_id=None by design.
    logs = list(AgentSessionLog.objects.filter(session=session, kind="output").order_by("id"))
    assert any("first chunk" in log.data for log in logs)
    assert any("second chunk" in log.data for log in logs)
    assert all(log.turn_id == turn.id for log in logs)


# --------------------------------------------------------------------------
# provision_session_task tests
# --------------------------------------------------------------------------


@pytest.fixture
def provision_user(db):
    u = User.objects.create_user(username="prov", password="p")
    APIKey.create_key(u, "test-key")
    usk = UserSpritesKey(user=u)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    cred = UserCredential(user=u, kind="provider:anthropic")
    cred.set_value("fake-anthropic-key")
    cred.save()
    return u


def _make_pending_session(user):
    agent = Agent.objects.create(
        user=user, name="a", model="anthropic/claude-sonnet-4-6", runtime="claude", version=1
    )
    session = AgentSession.objects.create(
        user=user,
        agent=agent,
        runtime="claude",
        prompt="hi",
        sprite_name="sprite-prov",
        runtime_session_id="11111111-2222-3333-4444-555555555555",
        status="pending",
    )
    turn = SessionTurn.objects.create(session=session, turn_number=1, prompt="hi", status="pending")
    return session, turn


@pytest.mark.django_db
def test_provision_task_enqueues_execute_turn_on_success(provision_user, fake_sprites, mocker):
    session, turn = _make_pending_session(provision_user)
    defer_spy = mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    provision_session_task(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Sprite was created and set up.
    assert "sprite-prov" in fake_sprites.sprites
    # Downstream turn task was enqueued with the same args.
    kwargs = defer_spy.call_args.kwargs
    assert kwargs["session_id"] == str(session.id)
    assert kwargs["turn_id"] == turn.id
    assert kwargs["prompt"] == "hi"
    assert kwargs["mode"] == "run"

    session.refresh_from_db()
    assert session.status == "pending"  # worker hands off to execute_turn


@pytest.mark.django_db
def test_provision_task_marks_failed_on_sprite_error(provision_user, fake_sprites, mocker):
    session, turn = _make_pending_session(provision_user)
    fake_sprites.raise_on_create(SpriteError("boom"))
    defer_spy = mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    provision_session_task(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    session.refresh_from_db()
    turn.refresh_from_db()
    assert session.status == "failed"
    # sprite_name is cleared so DELETE /sessions doesn't try to delete a
    # Sprite that was never created.
    assert session.sprite_name == ""
    assert turn.status == "failed"
    assert turn.ended_at is not None
    assert defer_spy.call_count == 0

    # An stderr log chunk captures the message for the stream endpoint.
    logs = AgentSessionLog.objects.filter(session=session, stream="stderr")
    assert logs.exists()


@pytest.mark.django_db
def test_provision_task_skips_if_session_terminated(provision_user, fake_sprites, mocker):
    session, turn = _make_pending_session(provision_user)
    session.status = "terminated"
    session.save(update_fields=["status"])
    defer_spy = mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    provision_session_task(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Nothing provisioned, nothing enqueued.
    assert fake_sprites.created == []
    assert defer_spy.call_count == 0


@pytest.mark.django_db
def test_provision_task_skips_if_session_deleted(provision_user, fake_sprites, mocker):
    """Race: user calls DELETE /sessions on a pending session after the
    view enqueued provision_session_task but before the worker picks it up.
    The task must swallow AgentSession.DoesNotExist and return cleanly."""
    session, turn = _make_pending_session(provision_user)
    session_id = str(session.id)
    turn_id = turn.id
    mocker.patch("agent_on_demand.session_service.tasks.destroy_session_task.defer")
    session.delete()  # cascades to turn + logs
    defer_spy = mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    # Does not raise.
    provision_session_task(
        session_id=session_id,
        turn_id=turn_id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    assert fake_sprites.created == []
    assert defer_spy.call_count == 0


@pytest.mark.django_db
def test_execute_turn_skips_if_session_deleted(user, mocker):
    """Race: DELETE /sessions fires between POST /prompt enqueueing execute_turn
    and the worker picking it up. The task must no-op rather than raise."""
    session, turn = _make_session_and_turn(user)
    session_id = str(session.id)
    turn_id = turn.id
    mocker.patch("agent_on_demand.session_service.tasks.destroy_session_task.defer")
    session.delete()
    resume_spy = mocker.patch("agent_on_demand.session_service.tasks.resume_session")

    # Does not raise.
    execute_turn(
        session_id=session_id,
        turn_id=turn_id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Sprite is never resumed — we bail before the runtime is touched.
    assert resume_spy.call_count == 0


@pytest.mark.django_db
def test_execute_turn_skips_finalization_if_session_deleted_mid_turn(user, mocker):
    """Race: user terminates then deletes a session while the turn body is
    running. The sprite command returns (ExecError or clean), and the
    post-run `session.refresh_from_db` sees the row gone. Skip the
    finalization writes (they'd have been cascade-deleted anyway) and don't
    let DoesNotExist bubble out to the task's error reporter."""
    session, turn = _make_session_and_turn(user)
    _patch_sprite(mocker, "success")
    mocker.patch("agent_on_demand.session_service.tasks.destroy_session_task.defer")

    # Delete the session on the very first refresh_from_db call (which is
    # the finalization refresh — the sprite command has already returned).
    original_refresh = AgentSession.refresh_from_db

    def deleting_refresh(self, *args, **kwargs):
        if self.pk == session.pk:
            AgentSession.objects.filter(pk=self.pk).delete()
            return original_refresh(self, *args, **kwargs)
        return original_refresh(self, *args, **kwargs)

    mocker.patch.object(AgentSession, "refresh_from_db", deleting_refresh)

    # Does not raise.
    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Session row + turn row are gone (cascade-deleted).
    assert not AgentSession.objects.filter(pk=session.pk).exists()
    assert not SessionTurn.objects.filter(pk=turn.pk).exists()


@pytest.mark.django_db
def test_provision_task_runs_with_no_runtime_credential(provision_user, fake_sprites, mocker):
    """Credentials are now dumped wholesale into /tmp/aod-env; provisioning
    no longer short-circuits on missing creds (the session-create HTTP gate
    is what prevents this from reaching the worker). This just confirms the
    worker path succeeds with zero credentials."""
    session, turn = _make_pending_session(provision_user)
    UserCredential.objects.filter(user=provision_user).delete()
    defer_spy = mocker.patch("agent_on_demand.session_service.tasks.execute_turn.defer")

    provision_session_task(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="hi",
        mode="run",
        timeout=10.0,
    )

    # Provisioning proceeded; turn execution is the worker's next step.
    assert defer_spy.call_count == 1


# --------------------------------------------------------------------------
# destroy_session_task tests
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_destroy_task_deletes_sprite(provision_user, fake_sprites):
    destroy_session_task(user_id=provision_user.id, sprite_name="aod-xyz")
    assert fake_sprites.deleted == ["aod-xyz"]


@pytest.mark.django_db
def test_destroy_task_noop_when_user_gone(fake_sprites):
    """If the user row is gone by the time the worker picks up, skip
    cleanup rather than raise. The Sprite will time out server-side."""
    destroy_session_task(user_id=999_999, sprite_name="aod-xyz")
    assert fake_sprites.deleted == []


@pytest.mark.django_db
def test_destroy_task_swallows_sprite_errors(provision_user, fake_sprites, mocker):
    """Matches the pre-existing `best_effort_delete` contract — errors are
    logged, not raised. Re-raising would let Procrastinate keep retrying a
    call that might never succeed."""
    mocker.patch.object(fake_sprites, "delete_sprite", side_effect=SpriteError("transient"))
    destroy_session_task(user_id=provision_user.id, sprite_name="aod-xyz")
    # Assertion is "no exception raised".


# --------------------------------------------------------------------------
# Producer robustness tests
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_create_retries_on_transient_failure(user, mocker):
    """bulk_create raises once then succeeds → chunks land in DB, no exhaustion event."""
    session, turn = _make_session_and_turn(user)

    call_count = 0
    original_bulk_create = AgentSessionLog.objects.bulk_create

    def flaky_bulk_create(objs, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("transient DB error")
        return original_bulk_create(objs, **kwargs)

    mocker.patch.object(AgentSessionLog.objects, "bulk_create", side_effect=flaky_bulk_create)
    mocker.patch("agent_on_demand.session_service.tasks.time.sleep")
    mock_posthog = mocker.patch("posthog.capture")

    def drive_output(*_, **__):
        writer = mock_cmd.stdout
        writer.write(b"chunk-a\n")
        writer.write(b"chunk-b\n")

    _, mock_cmd = _patch_sprite(mocker, "success")
    mock_cmd.run.side_effect = drive_output

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    # All chunks must be in DB
    logs = list(AgentSessionLog.objects.filter(session=session).order_by("id"))
    assert any("chunk-a" in log.data for log in logs)
    assert any("chunk-b" in log.data for log in logs)

    # Exhaustion posthog event must NOT have been emitted
    exhaustion_calls = [
        c
        for c in mock_posthog.call_args_list
        if c.args and c.args[0] == "session.log_write_retry_exhausted"
    ]
    assert exhaustion_calls == []


@pytest.mark.django_db
def test_bulk_create_exhausts_retries_and_raises(user, mocker):
    """bulk_create always raises → posthog exhaustion event captured, task re-raises."""
    session, turn = _make_session_and_turn(user)

    mocker.patch.object(
        AgentSessionLog.objects,
        "bulk_create",
        side_effect=Exception("persistent DB error"),
    )
    mocker.patch("agent_on_demand.session_service.tasks.time.sleep")
    mock_posthog = mocker.patch("posthog.capture")

    def drive_output(*_, **__):
        writer = mock_cmd.stdout
        writer.write(b"some chunk\n")

    _, mock_cmd = _patch_sprite(mocker, "success")
    mock_cmd.run.side_effect = drive_output

    # After exhausting all retries _flush_buffer re-raises, so the task itself raises.
    with pytest.raises(Exception, match="persistent DB error"):
        execute_turn(
            session_id=str(session.id),
            turn_id=turn.id,
            prompt="t",
            mode="run",
            timeout=10.0,
        )

    exhaustion_calls = [
        c
        for c in mock_posthog.call_args_list
        if c.args and c.args[0] == "session.log_write_retry_exhausted"
    ]
    assert len(exhaustion_calls) == 1
    props = exhaustion_calls[0].kwargs.get("properties", {})
    assert props.get("dropped_chunks", 0) > 0


@pytest.mark.django_db
def test_queue_full_drops_chunk_and_counts():
    """TaggingQueueWriter with full queue increments drop_count and doesn't raise."""
    q: queue.Queue = queue.Queue(maxsize=1)
    writer = TaggingQueueWriter(q, "stdout")

    # Fill the queue so the next put will timeout
    q.put_nowait(object())

    # This write should be dropped, not raise
    result = writer.write(b"dropped chunk")

    assert writer.drop_count == 1
    assert result == len(b"dropped chunk")


@pytest.mark.django_db
def test_output_chunks_dropped_posthog_event(user, mocker):
    """When drop_count > 0 after the turn, session.output_chunks_dropped is captured."""
    session, turn = _make_session_and_turn(user)
    mock_posthog = mocker.patch("posthog.capture")

    original_patch_sprite = _patch_sprite(mocker, "success")
    _, mock_cmd = original_patch_sprite

    original_init = TaggingQueueWriter.__init__

    # Track created writers so we can set drop_count after run
    writers_created = []

    def tracking_init(self, q, stream):
        original_init(self, q, stream)
        writers_created.append(self)

    mocker.patch.object(TaggingQueueWriter, "__init__", tracking_init)

    def drive_and_drop(*_, **__):
        # Simulate drops by directly incrementing drop_count on the writers
        for w in writers_created:
            w.drop_count = 3

    mock_cmd.run.side_effect = drive_and_drop

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    drop_calls = [
        c
        for c in mock_posthog.call_args_list
        if c.args and c.args[0] == "session.output_chunks_dropped"
    ]
    assert len(drop_calls) == 1


@pytest.mark.django_db
def test_cmd_thread_leak_detected(user, mocker):
    """When cmd_thread.is_alive() returns True after join, posthog captures session.cmd_thread_leaked."""
    session, turn = _make_session_and_turn(user)
    _patch_sprite(mocker, "success")
    mock_posthog = mocker.patch("posthog.capture")

    mocker.patch.object(threading.Thread, "is_alive", return_value=True)

    execute_turn(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    leak_calls = [
        c
        for c in mock_posthog.call_args_list
        if c.args and c.args[0] == "session.cmd_thread_leaked"
    ]
    assert len(leak_calls) == 1
