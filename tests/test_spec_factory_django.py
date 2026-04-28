"""ORM-integration coverage for `build_spec_for_session`.

This is the integration layer for the rehydration: every test exercises a
real Django ORM round-trip — `User.objects.create_user`, `Agent.objects.create`,
`AgentSession.objects.create`, `SessionResource.objects.create` — to confirm
that persisted session state really translates into the SessionSpec shape
that provisioning and turn execution consume.

The mutation-killable per-branch assertions live in the sync sibling file
``tests/test_spec_factory.py`` (no Django imports, runnable under hammett).
This file is the safety net that catches drift between the in-memory duck
types we stub there and the actual ORM-row attribute set.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth.models import User

from agent_on_demand.models import Agent, AgentSession, Environment, SessionResource
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service.spec_factory import build_spec_for_session


@pytest.fixture
def user(db):
    return User.objects.create_user(username="sfuser", password="x")


@pytest.mark.django_db
def test_no_agent_yields_empty_collections(user):
    """Sessions without an attached agent (legacy or one-off prompts) must
    still produce a SessionSpec — model is empty, mcp_servers and skills
    are empty lists."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    spec = build_spec_for_session(session)
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []


@pytest.mark.django_db
def test_mcp_server_optional_fields_default(user):
    """An MCP server entry with only `name` set must rehydrate with every
    optional field falling back to its documented default — not raise on
    missing keys."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        mcp_servers=[{"name": "minimal"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )

    spec = build_spec_for_session(session)
    assert len(spec.mcp_servers) == 1
    s = spec.mcp_servers[0]
    assert s.name == "minimal"
    assert s.type == "url"
    assert s.url == ""
    assert s.headers == {}
    assert s.command == ""
    assert s.args == []
    assert s.env == {}


@pytest.mark.django_db
def test_github_skill_with_name(user):
    """A github skill with a name must rehydrate as SkillSpec(name=,
    source=, content=None)."""
    agent = Agent.objects.create(
        user=user,
        name="A",
        model="anthropic/claude-sonnet-4-6",
        runtime="claude",
        skills=[{"type": "github", "source": "owner/skills-repo", "name": "specific"}],
        version=1,
    )
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", agent=agent, status="pending"
    )

    spec = build_spec_for_session(session)
    s = spec.skills[0]
    assert s.name == "specific"
    assert s.source == "owner/skills-repo"
    assert s.content is None


@pytest.mark.django_db
def test_github_skill_without_name(user):
    """Github skill name is optional — omit → install whole repo. Must
    rehydrate as SkillSpec(name=None, source=, content=None)."""
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

    spec = build_spec_for_session(session)
    s = spec.skills[0]
    assert s.name is None
    assert s.source == "owner/whole-repo"
    assert s.content is None


@pytest.mark.django_db
def test_inline_skill_default_type(user):
    """A skill without `type` defaults to inline — must rehydrate as
    SkillSpec(name=, content=, source=None)."""
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

    spec = build_spec_for_session(session)
    s = spec.skills[0]
    assert s.name == "web-search"
    assert s.content is not None
    assert "body" in s.content
    assert s.source is None


@pytest.mark.django_db
def test_session_resources_token_set_and_unset(user):
    """Two session resources, one with a token and one without, must both
    materialize as RepoSpec — token preserved when set, None when not."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    with_token = SessionResource.objects.create(
        session=session,
        resource_type="github_repository",
        url="https://github.com/owner/with-token",
        mount_path="/repos/with-token",
    )
    with_token.set_token("ghp_secret")
    with_token.save()
    SessionResource.objects.create(
        session=session,
        resource_type="github_repository",
        url="https://github.com/owner/no-token",
        mount_path="/repos/no-token",
    )

    spec = build_spec_for_session(session)
    by_url = {r.url: r for r in spec.repos}
    assert by_url["https://github.com/owner/with-token"].token == "ghp_secret"
    assert by_url["https://github.com/owner/with-token"].mount_path == "/repos/with-token"
    assert by_url["https://github.com/owner/no-token"].token is None
    assert by_url["https://github.com/owner/no-token"].mount_path == "/repos/no-token"


@pytest.mark.django_db
def test_runtime_session_id_none_passes_through_as_none(user):
    """A session with `runtime_session_id=None` (the default for fresh
    sessions) must rehydrate to None — not the string "None"."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    assert session.runtime_session_id is None

    spec = build_spec_for_session(session)
    assert spec.runtime_session_id is None


@pytest.mark.django_db
def test_runtime_session_id_uuid_stringified(user):
    """A UUID `runtime_session_id` must be stringified — downstream consumers
    expect a plain string, not a UUID object."""
    rsid = uuid.uuid4()
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        status="pending",
        runtime_session_id=rsid,
    )

    spec = build_spec_for_session(session)
    assert spec.runtime_session_id == str(rsid)
    assert isinstance(spec.runtime_session_id, str)


@pytest.mark.django_db
def test_pass_through_fields_match_orm_state(user):
    """Pass-through fields rehydrate against real ORM rows — drift safety
    net for the duck-typed sync tests in `test_spec_factory.py`. A real
    `User`, `Environment`, `backend_handle`, `runtime`, and UUID
    `runtime_session_id` must all flow into the spec exactly."""
    environment = Environment.objects.create(user=user, name="prod-env", version=1)
    rsid = uuid.uuid4()
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        status="pending",
        environment=environment,
        backend_handle="aod-abc123",
        runtime_session_id=rsid,
    )

    spec = build_spec_for_session(session)
    assert spec.name == "aod-abc123"
    assert spec.user is session.user
    assert spec.environment is session.environment
    assert spec.runtime is RUNTIMES["claude"]
    assert spec.runtime_session_id == str(rsid)


@pytest.mark.django_db
def test_backend_defaults_to_sprites_on_new_session(user):
    """Existing rows and new sessions created without an explicit backend
    pick up the model default — `"sprites"` — and that value threads
    through to the spec."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    assert session.backend == "sprites"
    spec = build_spec_for_session(session)
    assert spec.backend == "sprites"


@pytest.mark.django_db
def test_backend_persists_and_threads_through(user):
    """A non-default backend value persists across save/refresh and lands
    on the spec via `build_spec_for_session`."""
    session = AgentSession.objects.create(
        user=user, runtime="claude", prompt="t", status="pending", backend="modal"
    )
    session.refresh_from_db()
    assert session.backend == "modal"
    spec = build_spec_for_session(session)
    assert spec.backend == "modal"


@pytest.mark.django_db
def test_spec_name_reads_backend_handle(user):
    """`spec.name` reads `backend_handle` directly. The legacy `sprite_name`
    fallback (PR #258 dual-write soak) was removed when migration 0020
    dropped the column."""
    session = AgentSession.objects.create(
        user=user,
        runtime="claude",
        prompt="t",
        status="pending",
        backend_handle="new-handle",
    )
    assert build_spec_for_session(session).name == "new-handle"


@pytest.mark.django_db
def test_no_agent_with_resources(user):
    """The no-agent guard does not short-circuit resource iteration —
    real `SessionResource` rows must still rehydrate into `RepoSpec`
    even when `session.agent is None`. Drift safety against the sync
    test stub of `session.resources.all()`."""
    session = AgentSession.objects.create(user=user, runtime="claude", prompt="t", status="pending")
    SessionResource.objects.create(
        session=session,
        resource_type="github_repository",
        url="https://github.com/owner/repo",
        mount_path="/repos/repo",
    )

    spec = build_spec_for_session(session)
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []
    assert len(spec.repos) == 1
    assert spec.repos[0].url == "https://github.com/owner/repo"
    assert spec.repos[0].mount_path == "/repos/repo"
