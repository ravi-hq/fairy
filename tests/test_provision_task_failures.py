"""Cover provision_session_task error paths that test_tasks.py doesn't.

The existing test_tasks.py covers the ProvisionError stage-tagging path
through `test_provision_task_marks_failed_on_sprite_error`. These
tests fill the two adjacent paths:

  - `build_spec_for_session` raises (line 177-180): an agent or
    environment row went missing between turn enqueue and worker
    pickup. Must mark provision failed with the error in the log,
    not crash the worker.

  - `NoBackendCredentialsError` (line 193-195): a backend credential
    revoked between session create and provision-task pickup. Must
    surface as failed-stage="no_backend_credentials" telemetry.

A new file (not modifying tests/test_tasks.py) so this PR can land
independently of #152.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentSessionLog,
    APIKey,
    SessionTurn,
    UserBackendCredential,
    UserCredential,
)
from agent_on_demand.session_service.errors import NoBackendCredentialsError
from agent_on_demand.session_service.tasks import _provision_session_inner


@pytest.fixture(autouse=True)
def _stub_close_old_connections(mocker):
    """Same defense as tests/test_tasks.py — close_old_connections inside the
    task body would close the test DB connection."""
    mocker.patch("agent_on_demand.session_service.tasks.close_old_connections")


@pytest.fixture
def user(db):
    u = User.objects.create_user(username="provtest", password="x")
    APIKey.create_key(u, "k")
    bcred = UserBackendCredential(user=u, backend="sprites")
    bcred.set_token("fake-sprites")
    bcred.save()
    cred = UserCredential(user=u, kind="provider:anthropic")
    cred.set_value("fake")
    cred.save()
    return u


def _make_session_and_turn(user):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        version=1,
    )
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        backend_handle="aod-prov-x",
        agent=agent,
        status="pending",
    )
    turn = SessionTurn.objects.create(session=session, turn_number=1, prompt="t", status="pending")
    return session, turn


@pytest.mark.django_db
def test_provision_task_marks_failed_when_build_spec_raises(user, mocker):
    """Race: agent row deleted between turn enqueue and provision-task
    pickup. build_spec_for_session raises (likely AttributeError on
    None agent). The task must mark provision failed and log, not
    propagate the exception."""
    session, turn = _make_session_and_turn(user)
    mocker.patch(
        "agent_on_demand.session_service.tasks.build_spec_for_session",
        side_effect=ValueError("agent gone"),
    )

    _provision_session_inner(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    session.refresh_from_db()
    assert session.status == "failed"
    log = AgentSessionLog.objects.filter(session=session).order_by("id").first()
    assert log is not None
    assert "agent gone" in log.data


@pytest.mark.django_db
def test_provision_task_marks_failed_on_no_backend_credential(user, mocker):
    """Race: backend credential revoked between session-create (which passed
    the pre-check) and provision-task pickup. The task must record
    failed-stage='no_backend_credentials' and mark provision failed."""
    session, turn = _make_session_and_turn(user)
    # provision_session is the call inside the spanned try-block. Force it
    # to raise NoBackendCredentialsError.
    mocker.patch(
        "agent_on_demand.session_service.tasks.provision_session",
        side_effect=NoBackendCredentialsError("revoked"),
    )

    _provision_session_inner(
        session_id=str(session.id),
        turn_id=turn.id,
        prompt="t",
        mode="run",
        timeout=10.0,
    )

    session.refresh_from_db()
    assert session.status == "failed"
    log = AgentSessionLog.objects.filter(session=session).order_by("id").first()
    assert log is not None
    assert "revoked" in log.data
