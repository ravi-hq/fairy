"""UserQuota model — direct unit tests.

The HTTP-level quota tests in test_api.py exercise the locked
session-creation flow end-to-end. These tests pin the contract of
`active_session_count_for` directly so a refactor that, say, widens
ACTIVE_SESSION_STATUSES to include 'completed' (causing every user to
permanently saturate their quota) can't slip past CI even if the
HTTP-level tests still incidentally pass.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import AgentSession, UserQuota
from agent_on_demand.models.quota import ACTIVE_SESSION_STATUSES


@pytest.fixture
def user(db):
    return User.objects.create_user(username="quota-tests", password="x")


@pytest.mark.django_db
def test_active_session_count_only_counts_pending_and_running(user):
    """Terminal statuses (completed, failed, terminated) must not count
    against the user's concurrent quota. If they did, every user would
    eventually be locked out forever."""
    AgentSession.objects.create(user=user, runtime="claude", prompt="p", status="pending")
    AgentSession.objects.create(user=user, runtime="claude", prompt="r", status="running")
    AgentSession.objects.create(user=user, runtime="claude", prompt="c", status="completed")
    AgentSession.objects.create(user=user, runtime="claude", prompt="f", status="failed")
    AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="terminated")

    assert UserQuota.active_session_count_for(user) == 2


@pytest.mark.django_db
def test_active_session_count_scoped_to_user(user):
    """One user's active sessions must not contribute to another user's count."""
    other = User.objects.create_user(username="other-quota-user", password="x")
    AgentSession.objects.create(user=other, runtime="claude", prompt="o", status="running")
    AgentSession.objects.create(user=user, runtime="claude", prompt="m", status="running")

    assert UserQuota.active_session_count_for(user) == 1
    assert UserQuota.active_session_count_for(other) == 1


@pytest.mark.django_db
def test_active_session_count_is_zero_when_user_has_no_sessions(user):
    assert UserQuota.active_session_count_for(user) == 0


def test_active_session_statuses_constant_is_only_pending_and_running():
    """Pin the constant so any change is forced through review. Adding
    'completed' here would silently break the per-user cap; removing
    'pending' would let users start unlimited sessions during provisioning."""
    assert ACTIVE_SESSION_STATUSES == ("pending", "running")


@pytest.mark.django_db
def test_user_quota_str_includes_user(user):
    quota = UserQuota.objects.create(user=user, max_concurrent_sessions=3)
    assert str(user) in str(quota)
    assert "quota" in str(quota).lower()
