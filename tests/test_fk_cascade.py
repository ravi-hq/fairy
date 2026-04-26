"""Pin foreign-key on_delete behaviors across the schema.

Each FK has a deliberate `on_delete` choice that matters for
operational correctness:
  - `CASCADE` on user-owned resources means deleting a User wipes all
    their data (used in account deletion + ops cleanup)
  - `SET_NULL` on Agent.environment means deleting an Environment
    leaves orphaned agents pointing at NULL (the API blocks this
    via environment_delete's session-existence check, but admin or
    direct DB ops can still trigger it)
  - `CASCADE` on AgentVersion → Agent and EnvironmentVersion →
    Environment means version history disappears with the parent
    (the parent is the source of truth for "exists at all")

A schema migration that changed any of these silently would break
the assumed behavior in places that depend on it. This file is the
contract test — each switch has to update the test deliberately.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentVersion,
    Environment,
    EnvironmentVersion,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username="cascadeuser", password="x")


@pytest.mark.django_db
def test_deleting_user_cascades_to_agents(user):
    a = Agent.objects.create(
        user=user, name="A", model="anthropic/claude-sonnet-4-6", runtime="claude", version=1
    )
    user.delete()
    assert not Agent.objects.filter(pk=a.pk).exists()


@pytest.mark.django_db
def test_deleting_user_cascades_to_environments(user):
    e = Environment.objects.create(user=user, name="E", version=1)
    user.delete()
    assert not Environment.objects.filter(pk=e.pk).exists()


@pytest.mark.django_db
def test_deleting_user_cascades_to_sessions(user):
    s = AgentSession.objects.create(user=user, runtime="claude", prompt="x", status="completed")
    user.delete()
    assert not AgentSession.objects.filter(pk=s.pk).exists()


@pytest.mark.django_db
def test_deleting_environment_sets_null_on_agent(user):
    """`Agent.environment` is SET_NULL — a hard env delete leaves the agent
    intact with environment_id=None. The API blocks this delete via the
    sessions-existence check; this test pins the behavior for the admin /
    direct-DB path."""
    e = Environment.objects.create(user=user, name="E", version=1)
    a = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        environment=e,
        version=1,
    )
    e.delete()
    a.refresh_from_db()
    assert a.environment_id is None


@pytest.mark.django_db
def test_deleting_environment_sets_null_on_session(user):
    """`AgentSession.environment` is also SET_NULL. Pinning here so a
    hypothetical migration to PROTECT doesn't accidentally lock out the
    admin from cleaning up environments referenced by old terminal
    sessions."""
    e = Environment.objects.create(user=user, name="E", version=1)
    s = AgentSession.objects.create(
        user=user, runtime="claude", prompt="x", environment=e, status="completed"
    )
    e.delete()
    s.refresh_from_db()
    assert s.environment_id is None


@pytest.mark.django_db
def test_deleting_agent_cascades_to_versions(user):
    """Version history disappears with the parent — the parent's row is
    the source of truth for 'exists at all'."""
    a = Agent.objects.create(
        user=user, name="A", model="anthropic/claude-sonnet-4-6", runtime="claude", version=1
    )
    AgentVersion.objects.create(agent=a, version=1, name=a.name, model=a.model, runtime=a.runtime)
    agent_pk = a.pk
    a.delete()
    assert not AgentVersion.objects.filter(agent_id=agent_pk).exists()


@pytest.mark.django_db
def test_deleting_environment_cascades_to_versions(user):
    e = Environment.objects.create(user=user, name="E", version=1)
    EnvironmentVersion.objects.create(
        environment=e,
        version=1,
        name=e.name,
        packages=e.packages,
        env_vars=e.env_vars,
        setup_script=e.setup_script,
        networking_type=e.networking_type,
        networking_config=e.networking_config,
    )
    env_pk = e.pk
    e.delete()
    assert not EnvironmentVersion.objects.filter(environment_id=env_pk).exists()
