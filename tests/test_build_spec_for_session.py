"""Cover `_build_spec_for_session` mcp_servers/skills rehydration paths.

The function pulls Agent.mcp_servers and Agent.skills (both JSONField
lists) and rehydrates each into an McpServerSpec / SkillSpec. Without
agents that carry these fields, the rehydration loops are unexecuted.

A bug that, say, swapped the field name (`name` → `id`) would silently
break MCP servers and skills at session-create. Pin both rehydration
shapes here.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import Agent, AgentSession
from agent_on_demand.session_service.tasks import _build_spec_for_session


@pytest.fixture
def user(db):
    return User.objects.create_user(username="bsuser", password="x")


@pytest.mark.django_db
def test_build_spec_rehydrates_url_mcp_server(user):
    """A URL-type MCP server in Agent.mcp_servers must round-trip into the
    session spec with all fields preserved (name, url, headers)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        mcp_servers=[
            {
                "name": "github",
                "type": "url",
                "url": "https://mcp.github.com/mcp",
                "headers": {"Authorization": "Bearer ${GH_TOKEN}"},
            }
        ],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )

    spec = _build_spec_for_session(session)
    assert len(spec.mcp_servers) == 1
    s = spec.mcp_servers[0]
    assert s.name == "github"
    assert s.type == "url"
    assert s.url == "https://mcp.github.com/mcp"
    assert s.headers == {"Authorization": "Bearer ${GH_TOKEN}"}


@pytest.mark.django_db
def test_build_spec_rehydrates_stdio_mcp_server(user):
    """A stdio-type MCP server with command/args/env must rehydrate fully."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        mcp_servers=[
            {
                "name": "local",
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@some/mcp"],
                "env": {"DEBUG": "1"},
            }
        ],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )
    spec = _build_spec_for_session(session)
    s = spec.mcp_servers[0]
    assert s.type == "stdio"
    assert s.command == "npx"
    assert s.args == ["-y", "@some/mcp"]
    assert s.env == {"DEBUG": "1"}


@pytest.mark.django_db
def test_build_spec_rehydrates_inline_skill(user):
    """An inline skill (with content) must rehydrate as SkillSpec(name=,
    content=, source=None)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"name": "web-search", "content": "---\nname: web-search\n---\nbody"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )
    spec = _build_spec_for_session(session)
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name == "web-search"
    assert s.content is not None
    assert "body" in s.content
    assert s.source is None


@pytest.mark.django_db
def test_build_spec_rehydrates_github_skill(user):
    """A github skill must rehydrate with source set; name is optional
    (omit → install all skills from the repo)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/skills-repo", "name": "specific-skill"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )
    spec = _build_spec_for_session(session)
    s = spec.skills[0]
    assert s.source == "owner/skills-repo"
    assert s.name == "specific-skill"
    assert s.content is None


@pytest.mark.django_db
def test_build_spec_github_skill_without_name(user):
    """Github skill name is optional — omit → install whole repo."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/whole-repo"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )
    spec = _build_spec_for_session(session)
    s = spec.skills[0]
    assert s.source == "owner/whole-repo"
    assert s.name is None


@pytest.mark.django_db
def test_build_spec_with_no_agent(user):
    """Sessions without an attached agent (legacy or one-off prompts) must
    still produce a SessionSpec — model is empty, mcp_servers and skills
    are empty lists."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    spec = _build_spec_for_session(session)
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []
