"""pytest-django tests for build_spec_for_session in spec_factory.py.

Covers every branch in the rehydration: no-agent sessions, MCP server
optional field defaults, skill type dispatch (github with/without name,
inline), session resources with/without tokens, and runtime_session_id
handling.
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
def test_no_agent_yields_empty_lists(user):
    """Session with no agent → empty mcp_servers, skills, and model."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", status="pending"
    )
    spec = build_spec_for_session(session)
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []


@pytest.mark.django_db
def test_mcp_servers_optional_fields_default(user):
    """Agent with a minimal MCP server entry → McpServerSpec with all
    optional fields at their defaults (type='url', headers={}, command='',
    args=[], env={})."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        mcp_servers=[{"name": "minimal"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", agent=agent, status="pending"
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
    """Agent skill type='github' with a name → SkillSpec(name=..., source=..., content=None)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/repo", "name": "my-skill"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name == "my-skill"
    assert s.source == "owner/repo"
    assert s.content is None


@pytest.mark.django_db
def test_github_skill_without_name(user):
    """Agent skill type='github' with no name → SkillSpec(name=None, source=..., content=None).

    Omitting name installs all skills from the repo."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/whole-repo"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name is None
    assert s.source == "owner/whole-repo"
    assert s.content is None


@pytest.mark.django_db
def test_inline_skill_without_type(user):
    """Agent skill without a type (defaults to inline) → SkillSpec(name=..., content=..., source=None)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"name": "search", "content": "---\nname: search\n---\nbody text"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", agent=agent, status="pending"
    )
    spec = build_spec_for_session(session)
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name == "search"
    assert s.content == "---\nname: search\n---\nbody text"
    assert s.source is None


@pytest.mark.django_db
def test_session_resources_token_and_no_token(user):
    """Two SessionResource rows — one with token, one without — both materialize
    as RepoSpec with token set/None respectively."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="hi", status="pending"
    )

    r1 = SessionResource(
        session=session,
        resource_type="github_repository",
        url="https://github.com/owner/private",
        mount_path="/workspace/private",
    )
    r1.set_token("ghp_secrettoken")
    r1.save()

    r2 = SessionResource.objects.create(
        session=session,
        resource_type="github_repository",
        url="https://github.com/owner/public",
        mount_path="/workspace/public",
    )

    spec = build_spec_for_session(session)
    assert len(spec.repos) == 2

    by_url = {r.url: r for r in spec.repos}
    assert by_url["https://github.com/owner/private"].token == "ghp_secrettoken"
    assert by_url["https://github.com/owner/public"].token is None


@pytest.mark.django_db
def test_runtime_session_id_none(user):
    """Session with runtime_session_id=None → SessionSpec.runtime_session_id=None."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="hi",
        status="pending",
        runtime_session_id=None,
    )
    spec = build_spec_for_session(session)
    assert spec.runtime_session_id is None


@pytest.mark.django_db
def test_runtime_session_id_stringified(user):
    """Session with a UUID runtime_session_id → stringified in the spec."""
    rid = uuid.uuid4()
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="hi",
        status="pending",
        runtime_session_id=rid,
    )
    spec = build_spec_for_session(session)
    assert spec.runtime_session_id == str(rid)
