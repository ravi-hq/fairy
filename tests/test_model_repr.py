"""Pin the `__str__` format of models exposed in the Django admin.

These are simple but worth pinning: a refactor that drops a `__str__`
falls back to Python's default `<App.Model object (1)>` representation,
which silently degrades the admin UX. Each test asserts the format
contains identifying tokens (name, version, status) so a future format
change still has to update the test deliberately.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentVersion,
    APIKey,
    Environment,
    EnvironmentVersion,
    SessionResource,
    SessionTurn,
    UserSpritesKey,
)
from agent_on_demand.models.sessions import AgentSessionLog


@pytest.fixture
def user(db):
    return User.objects.create_user(username="repruser", password="x")


@pytest.mark.django_db
def test_agent_str_contains_name_and_version(user):
    a = Agent.objects.create(
        user=user, name="My Agent", model="anthropic/claude-sonnet-4-6", runtime="claude", version=3
    )
    s = str(a)
    assert "My Agent" in s
    assert "v3" in s
    assert str(a.id) in s


@pytest.mark.django_db
def test_agent_version_str_includes_agent_name_and_version(user):
    a = Agent.objects.create(
        user=user, name="V Agent", model="anthropic/claude-sonnet-4-6", runtime="claude", version=2
    )
    av = AgentVersion.objects.create(
        agent=a, version=2, name=a.name, model=a.model, runtime=a.runtime
    )
    s = str(av)
    assert "V Agent" in s
    assert "v2" in s


@pytest.mark.django_db
def test_environment_str_contains_name_and_version(user):
    e = Environment.objects.create(user=user, name="env-x", version=1)
    s = str(e)
    assert "env-x" in s
    assert "v1" in s


@pytest.mark.django_db
def test_environment_version_str_includes_environment_name_and_version(user):
    e = Environment.objects.create(user=user, name="env-y", version=4)
    ev = EnvironmentVersion.objects.create(
        environment=e,
        version=4,
        name=e.name,
        packages=e.packages,
        env_vars=e.env_vars,
        setup_script=e.setup_script,
        networking_type=e.networking_type,
        networking_config=e.networking_config,
    )
    s = str(ev)
    assert "env-y" in s
    assert "v4" in s


@pytest.mark.django_db
def test_agent_session_str_contains_runtime_status_id(user):
    sess = AgentSession.objects.create(user=user, runtime="claude", prompt="hi", status="running")
    s = str(sess)
    assert "claude" in s
    assert "running" in s
    assert str(sess.id) in s


@pytest.mark.django_db
def test_session_turn_str_contains_turn_number_and_status(user):
    sess = AgentSession.objects.create(user=user, runtime="claude", prompt="hi", status="running")
    t = SessionTurn.objects.create(session=sess, turn_number=2, prompt="p", status="pending")
    s = str(t)
    assert "turn 2" in s
    assert "pending" in s


@pytest.mark.django_db
def test_session_resource_str_contains_type_url_path(user):
    sess = AgentSession.objects.create(user=user, runtime="claude", prompt="hi", status="running")
    r = SessionResource.objects.create(
        session=sess,
        resource_type="github_repository",
        url="https://github.com/o/r",
        mount_path="/repos/r",
    )
    s = str(r)
    assert "github_repository" in s
    assert "https://github.com/o/r" in s
    assert "/repos/r" in s


@pytest.mark.django_db
def test_agent_session_log_str_branches_for_stage_and_output(user):
    sess = AgentSession.objects.create(user=user, runtime="claude", prompt="hi", status="running")
    stage_log = AgentSessionLog.objects.create(
        session=sess, kind="stage", stage="install", state="started"
    )
    output_log = AgentSessionLog.objects.create(
        session=sess, kind="output", stream="stdout", data="hello world from runtime"
    )
    assert "stage:install:started" in str(stage_log)
    assert "stdout" in str(output_log)
    assert "hello world from runtime" in str(output_log)


@pytest.mark.django_db
def test_api_key_str_uses_prefix_and_name(user):
    k, _ = APIKey.create_key(user, "my-token-name")
    s = str(k)
    assert "my-token-name" in s
    assert k.key_prefix in s


@pytest.mark.django_db
def test_user_sprites_key_str_includes_user(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-token")
    usk.save()
    assert str(user) in str(usk)


# --- session_service.client.require_client raises when no key configured ---


@pytest.mark.django_db
def test_require_client_raises_when_no_sprites_key(user):
    """The session-creation hot path uses get_client; a few infrequent code
    paths use require_client which surfaces NoBackendCredentialsError. Pin
    the raise so a refactor that returned None-or-default can't slip past."""
    from agent_on_demand.session_service.client import require_client
    from agent_on_demand.session_service.errors import NoBackendCredentialsError

    with pytest.raises(NoBackendCredentialsError):
        require_client(user)
