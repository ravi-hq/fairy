from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from agent_on_demand.models import AgentSession, AgentSessionLog
from agent_on_demand.session_service.maintenance import (
    mark_stuck_sessions_failed,
    purge_old_session_logs,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="mt", password="pw")


def _backdate(session: AgentSession, *, hours=0, days=0, minutes=0) -> None:
    ts = timezone.now() - timedelta(hours=hours, days=days, minutes=minutes)
    AgentSession.objects.filter(pk=session.pk).update(updated_at=ts)


# --- purge_old_session_logs ---


@pytest.mark.django_db
def test_purge_deletes_old_terminal_sessions(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    old = AgentSession.objects.create(user=user, runtime="claude", prompt="", status="completed")
    _backdate(old, days=31)

    purge_old_session_logs(0)

    assert not AgentSession.objects.filter(pk=old.pk).exists()


@pytest.mark.django_db
def test_purge_skips_recent_terminal_sessions(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    recent = AgentSession.objects.create(user=user, runtime="claude", prompt="", status="completed")
    _backdate(recent, days=5)

    purge_old_session_logs(0)

    assert AgentSession.objects.filter(pk=recent.pk).exists()


@pytest.mark.django_db
def test_purge_skips_running_sessions(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    old_running = AgentSession.objects.create(
        user=user, runtime="claude", prompt="", status="running"
    )
    _backdate(old_running, days=31)

    purge_old_session_logs(0)

    assert AgentSession.objects.filter(pk=old_running.pk).exists()


@pytest.mark.django_db
def test_purge_cascades_to_logs(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    old = AgentSession.objects.create(user=user, runtime="claude", prompt="", status="completed")
    AgentSessionLog.objects.create(session=old, stream="stdout", data="hello\n")
    AgentSessionLog.objects.create(session=old, stream="stdout", data="world\n")
    _backdate(old, days=31)

    purge_old_session_logs(0)

    assert AgentSessionLog.objects.filter(session_id=old.pk).count() == 0


@pytest.mark.django_db
def test_purge_batches_correctly(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    mocker.patch("agent_on_demand.session_service.maintenance.PURGE_BATCH_SIZE", 3)
    for _ in range(7):
        s = AgentSession.objects.create(
            user=user, runtime="claude", prompt="", status="completed"
        )
        _backdate(s, days=31)

    assert AgentSession.objects.count() == 7
    purge_old_session_logs(0)
    assert AgentSession.objects.count() == 0


@pytest.mark.django_db
def test_purge_closes_db_connections(user, mocker):
    spy = mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")

    purge_old_session_logs(0)

    assert spy.call_count == 2  # entry + finally


# --- mark_stuck_sessions_failed ---


@pytest.mark.django_db
def test_watchdog_flips_stuck_running(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    stuck = AgentSession.objects.create(user=user, runtime="claude", prompt="", status="running")
    _backdate(stuck, minutes=20)

    mark_stuck_sessions_failed(0)

    stuck.refresh_from_db()
    assert stuck.status == "failed"


@pytest.mark.django_db
def test_watchdog_leaves_recent_running_alone(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    fresh = AgentSession.objects.create(user=user, runtime="claude", prompt="", status="running")
    _backdate(fresh, minutes=5)

    mark_stuck_sessions_failed(0)

    fresh.refresh_from_db()
    assert fresh.status == "running"


@pytest.mark.django_db
def test_watchdog_leaves_terminal_sessions_alone(user, mocker):
    mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")
    completed = AgentSession.objects.create(
        user=user, runtime="claude", prompt="", status="completed"
    )
    _backdate(completed, minutes=20)

    mark_stuck_sessions_failed(0)

    completed.refresh_from_db()
    assert completed.status == "completed"


@pytest.mark.django_db
def test_watchdog_closes_db_connections(user, mocker):
    spy = mocker.patch("agent_on_demand.session_service.maintenance.close_old_connections")

    mark_stuck_sessions_failed(0)

    assert spy.call_count == 2
