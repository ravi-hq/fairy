"""pytest-django unit tests for build_spec_for_session.

Covers every branch in the rehydration: no agent, mcp_servers with optional
fields defaulted, github skills with/without name, inline skills, SessionResource
rows with and without tokens, and runtime_session_id None vs UUID.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import Agent, AgentSession, SessionResource
from agent_on_demand.session_service.spec_factory import build_spec_for_session


@pytest.fixture
def user(db):
    return User.objects.create_user(username="sfuser", password="x")


@pytest.mark.django_db
def test_no_agent_produces_empty_collections(user):
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="p", status="pending")
    spec = build_spec_for_session(session)
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []


@pytest.mark.django_db
def test_mcp_server_all_optional_fields_defaulted(user):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        mcp_servers=[{"name": "minimal"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    assert len(spec.mcp_servers) == 1
    s = spec.mcp_servers[0]
    assert s.name == "minimal"
    assert s.type == "url"
    assert s.headers == {}
    assert s.command == ""
    assert s.args == []
    assert s.env == {}


@pytest.mark.django_db
def test_github_skill_with_name(user):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/repo", "name": "my-skill"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name == "my-skill"
    assert s.source == "owner/repo"
    assert s.content is None


@pytest.mark.django_db
def test_github_skill_without_name(user):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/whole-repo"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    s = spec.skills[0]
    assert s.name is None
    assert s.source == "owner/whole-repo"
    assert s.content is None


@pytest.mark.django_db
def test_inline_skill(user):
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"name": "search", "content": "---\nname: search\n---\nbody text"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    s = spec.skills[0]
    assert s.name == "search"
    assert s.content == "---\nname: search\n---\nbody text"
    assert s.source is None


@pytest.mark.django_db
def test_session_resources_with_and_without_token(user):
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="p", status="pending")
    r1 = SessionResource(
        session=session,
        resource_type="github_repository",
        url="https://github.com/org/private",
        mount_path="/workspace/private",
    )
    r1.set_token("ghp_secret")
    r1.save()

    SessionResource.objects.create(
        session=session,
        resource_type="github_repository",
        url="https://github.com/org/public",
        mount_path="/workspace/public",
    )

    spec = build_spec_for_session(session)
    assert len(spec.repos) == 2
    by_mount = {r.mount_path: r for r in spec.repos}
    assert by_mount["/workspace/private"].token == "ghp_secret"
    assert by_mount["/workspace/public"].token is None


@pytest.mark.django_db
def test_runtime_session_id_none(user):
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", status="pending", runtime_session_id=None
    )
    spec = build_spec_for_session(session)
    assert spec.runtime_session_id is None


@pytest.mark.django_db
def test_runtime_session_id_stringified(user):
    rid = uuid.uuid4()
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="p", status="pending", runtime_session_id=rid
    )
    spec = build_spec_for_session(session)
    assert spec.runtime_session_id == str(rid)
