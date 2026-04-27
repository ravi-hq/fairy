"""Direct unit tests for `build_spec_for_session`.

Mutation-tested. Each test pins one mutation-killable property of the
ORM-row → SessionSpec rehydration:

  - The no-agent branch fixes ``model = ""`` and empty ``mcp_servers`` /
    ``skills`` lists, but resources are still rehydrated.
  - ``agent.mcp_servers or []`` and ``agent.skills or []`` collapse
    ``None`` to an empty iterable — a mutant that drops the ``or`` would
    raise ``TypeError`` instead.
  - Each `McpServerSpec` field carries the documented default when its
    key is absent: ``type="url"``, ``url=""``, ``headers={}``,
    ``command=""``, ``args=[]``, ``env={}``.
  - For github skills, ``name`` is forwarded verbatim — present-and-set
    or absent (``None``) — and ``source`` carries the repo identifier.
  - For inline skills (no ``type=="github"``), ``name`` and ``content``
    are required and ``source`` stays ``None``.
  - `session.resources.all()` is iterated and each row becomes a
    ``RepoSpec(url, mount_path, token=r.get_token())``.
  - ``runtime_session_id`` is stringified when truthy and preserved as
    ``None`` when falsy — ``str(None)`` would yield ``"None"``, the wrong
    answer.
  - ``session.user``, ``session.environment``, ``session.sprite_name``
    flow through unchanged.
  - The ``session.runtime`` string is looked up in ``RUNTIMES`` and the
    looked-up Runtime instance is what lands on the spec.

Tests are sync, no Django imports — required so hammett (mutmut's
runner) can execute them. ``AgentSession``, ``Agent``, ``Environment``,
``User``, and ``SessionResource`` are duck-typed via ``SimpleNamespace``
and dict literals.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.session_service.spec_factory import build_spec_for_session


def _resources(rows: list | None = None):
    """Stub a Django reverse-relation manager: ``session.resources.all()``."""
    items = list(rows or [])
    return SimpleNamespace(all=lambda: items)


def _resource(url="https://github.com/o/r", mount_path="/repos/r", token=None):
    return SimpleNamespace(url=url, mount_path=mount_path, get_token=lambda: token)


def _session(
    *,
    agent=None,
    user=None,
    runtime="claude",
    runtime_session_id=None,
    sprite_name="sprite-1",
    environment=None,
    resources=None,
):
    return SimpleNamespace(
        agent=agent,
        user=user if user is not None else SimpleNamespace(),
        runtime=runtime,
        runtime_session_id=runtime_session_id,
        sprite_name=sprite_name,
        environment=environment,
        resources=_resources(resources),
    )


def _agent(*, model="anthropic/claude-sonnet-4-6", mcp_servers=None, skills=None):
    return SimpleNamespace(model=model, mcp_servers=mcp_servers, skills=skills)


# ---------- no-agent branch ----------


def test_no_agent_session_yields_empty_model_and_collections():
    """``agent is None`` → ``model = ""`` and empty mcp/skills lists. Pin
    so a mutant that drops the guard or seeds these with anything other
    than empty is caught."""
    spec = build_spec_for_session(_session(agent=None))
    assert spec.model == ""
    assert spec.mcp_servers == []
    assert spec.skills == []


def test_no_agent_session_still_rehydrates_resources():
    """The agent guard must not short-circuit resource iteration —
    repos are populated from ``session.resources.all()`` even when
    ``agent is None``."""
    spec = build_spec_for_session(
        _session(
            agent=None,
            resources=[_resource(url="https://github.com/o/r", mount_path="/repos/r")],
        )
    )
    assert len(spec.repos) == 1
    assert spec.repos[0].url == "https://github.com/o/r"


# ---------- agent.model passes through ----------


def test_agent_model_is_carried_into_spec():
    """``agent.model`` becomes ``spec.model`` verbatim — no transformation."""
    spec = build_spec_for_session(
        _session(agent=_agent(model="anthropic/claude-sonnet-4-6"))
    )
    assert spec.model == "anthropic/claude-sonnet-4-6"


# ---------- mcp_servers `or []` defaulting ----------


def test_mcp_servers_none_yields_empty_list():
    """``agent.mcp_servers is None`` → empty list. Pin the ``or []`` so a
    mutant that drops it would TypeError on iteration."""
    spec = build_spec_for_session(_session(agent=_agent(mcp_servers=None)))
    assert spec.mcp_servers == []


def test_mcp_servers_empty_list_stays_empty():
    spec = build_spec_for_session(_session(agent=_agent(mcp_servers=[])))
    assert spec.mcp_servers == []


# ---------- McpServerSpec field defaults ----------


def test_mcp_server_minimal_uses_documented_defaults():
    """An MCP entry with only ``name`` set rehydrates to every default —
    one assertion per field so a default-swap mutant is killed
    individually."""
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "minimal"}]))
    )
    assert len(spec.mcp_servers) == 1
    s = spec.mcp_servers[0]
    assert s.name == "minimal"
    assert s.type == "url"
    assert s.url == ""
    assert s.headers == {}
    assert s.command == ""
    assert s.args == []
    assert s.env == {}


def test_mcp_server_name_is_required_field():
    """``name`` is a required dict key — it lands on the spec verbatim,
    no fallback. Pin so a mutant that swaps it with another key is
    caught."""
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "github-tools"}]))
    )
    assert spec.mcp_servers[0].name == "github-tools"


def test_mcp_server_type_default_is_url():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].type == "url"


def test_mcp_server_type_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x", "type": "stdio"}]))
    )
    assert spec.mcp_servers[0].type == "stdio"


def test_mcp_server_url_default_is_empty_string():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].url == ""


def test_mcp_server_url_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x", "url": "https://mcp.example/"}]))
    )
    assert spec.mcp_servers[0].url == "https://mcp.example/"


def test_mcp_server_headers_default_is_empty_dict():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].headers == {}


def test_mcp_server_headers_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                mcp_servers=[{"name": "x", "headers": {"Authorization": "Bearer t"}}]
            )
        )
    )
    assert spec.mcp_servers[0].headers == {"Authorization": "Bearer t"}


def test_mcp_server_command_default_is_empty_string():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].command == ""


def test_mcp_server_command_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x", "command": "npx"}]))
    )
    assert spec.mcp_servers[0].command == "npx"


def test_mcp_server_args_default_is_empty_list():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].args == []


def test_mcp_server_args_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(
            agent=_agent(mcp_servers=[{"name": "x", "args": ["-y", "@scope/pkg"]}])
        )
    )
    assert spec.mcp_servers[0].args == ["-y", "@scope/pkg"]


def test_mcp_server_env_default_is_empty_dict():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x"}]))
    )
    assert spec.mcp_servers[0].env == {}


def test_mcp_server_env_explicit_overrides_default():
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "x", "env": {"API_KEY": "v"}}]))
    )
    assert spec.mcp_servers[0].env == {"API_KEY": "v"}


def test_mcp_server_order_is_preserved():
    """Two servers in the agent list must appear in the spec in the same
    order — pin so a reversal mutant is caught."""
    spec = build_spec_for_session(
        _session(agent=_agent(mcp_servers=[{"name": "first"}, {"name": "second"}]))
    )
    assert [s.name for s in spec.mcp_servers] == ["first", "second"]


# ---------- skills `or []` defaulting ----------


def test_skills_none_yields_empty_list():
    """``agent.skills is None`` → empty list. Pin the ``or []`` so a
    mutant that drops it would TypeError on iteration."""
    spec = build_spec_for_session(_session(agent=_agent(skills=None)))
    assert spec.skills == []


def test_skills_empty_list_stays_empty():
    spec = build_spec_for_session(_session(agent=_agent(skills=[])))
    assert spec.skills == []


# ---------- github skill branch ----------


def test_github_skill_with_name_carries_name_and_source():
    """``type=="github"`` + named skill → ``SkillSpec(name=, source=,
    content=None)``. Pin every field individually so a swap mutant is
    caught."""
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                skills=[
                    {"type": "github", "source": "owner/skills-repo", "name": "specific"}
                ]
            )
        )
    )
    assert len(spec.skills) == 1
    s = spec.skills[0]
    assert s.name == "specific"
    assert s.source == "owner/skills-repo"
    assert s.content is None


def test_github_skill_without_name_has_name_none():
    """github skill ``name`` is optional — absent dict key must land on
    the spec as ``None`` (not ``""`` or KeyError)."""
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                skills=[{"type": "github", "source": "owner/whole-repo"}]
            )
        )
    )
    s = spec.skills[0]
    assert s.name is None
    assert s.source == "owner/whole-repo"
    assert s.content is None


def test_github_skill_dispatch_keyed_on_exact_string():
    """The branch is ``s.get("type") == "github"`` — case-sensitive
    equality. Anything else (including ``"GitHub"``) takes the inline
    branch, which would KeyError on missing ``content``."""
    inline = build_spec_for_session(
        _session(
            agent=_agent(skills=[{"name": "n", "content": "body"}])
        )
    )
    assert inline.skills[0].source is None
    assert inline.skills[0].content == "body"


# ---------- inline skill branch ----------


def test_inline_skill_no_type_carries_name_and_content():
    """A skill dict without ``type`` defaults to inline → ``SkillSpec(name=,
    content=, source=None)``."""
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                skills=[{"name": "web-search", "content": "---\nname: web-search\n---\nbody"}]
            )
        )
    )
    s = spec.skills[0]
    assert s.name == "web-search"
    assert s.content == "---\nname: web-search\n---\nbody"
    assert s.source is None


def test_inline_skill_with_non_github_type_takes_inline_branch():
    """Any ``type`` value other than ``"github"`` lands in the inline
    branch — pin so a mutant that flips the equality to ``!=`` is
    caught."""
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                skills=[{"type": "inline", "name": "n", "content": "c"}]
            )
        )
    )
    s = spec.skills[0]
    assert s.name == "n"
    assert s.content == "c"
    assert s.source is None


def test_skills_order_is_preserved():
    spec = build_spec_for_session(
        _session(
            agent=_agent(
                skills=[
                    {"name": "first", "content": "a"},
                    {"name": "second", "content": "b"},
                ]
            )
        )
    )
    assert [s.name for s in spec.skills] == ["first", "second"]


# ---------- resources iteration ----------


def test_resources_are_iterated_via_all():
    """Each row from ``session.resources.all()`` becomes a ``RepoSpec``.
    Pin the call to ``.all()`` so a mutant that switches to direct
    iteration of the manager is caught."""
    calls: list = []

    def all_recorder():
        calls.append("called")
        return [_resource(url="https://github.com/o/r", mount_path="/repos/r")]

    session = _session()
    session.resources = SimpleNamespace(all=all_recorder)
    spec = build_spec_for_session(session)
    assert calls == ["called"]
    assert len(spec.repos) == 1


def test_resource_url_and_mount_path_pass_through():
    spec = build_spec_for_session(
        _session(
            resources=[
                _resource(url="https://github.com/o/r", mount_path="/repos/r")
            ]
        )
    )
    repo = spec.repos[0]
    assert repo.url == "https://github.com/o/r"
    assert repo.mount_path == "/repos/r"


def test_resource_token_pulled_from_get_token():
    """``RepoSpec.token`` comes from ``r.get_token()``, not ``r.token``.
    Pin so a mutant that drops the call (or reads a missing attr) is
    caught."""
    spec = build_spec_for_session(
        _session(
            resources=[
                _resource(
                    url="https://github.com/o/r",
                    mount_path="/repos/r",
                    token="ghp_secret",
                )
            ]
        )
    )
    assert spec.repos[0].token == "ghp_secret"


def test_resource_token_none_passes_through_as_none():
    spec = build_spec_for_session(
        _session(
            resources=[
                _resource(
                    url="https://github.com/o/r", mount_path="/repos/r", token=None
                )
            ]
        )
    )
    assert spec.repos[0].token is None


def test_resources_empty_yields_empty_repos():
    spec = build_spec_for_session(_session(resources=[]))
    assert spec.repos == []


def test_resources_order_is_preserved():
    spec = build_spec_for_session(
        _session(
            resources=[
                _resource(url="https://github.com/o/first", mount_path="/repos/first"),
                _resource(url="https://github.com/o/second", mount_path="/repos/second"),
            ]
        )
    )
    assert [r.url for r in spec.repos] == [
        "https://github.com/o/first",
        "https://github.com/o/second",
    ]


# ---------- runtime_session_id stringification ----------


def test_runtime_session_id_none_passes_through_as_none():
    """Falsy ``runtime_session_id`` must rehydrate to ``None`` — never the
    string ``"None"`` (which a naive ``str(...)`` would produce)."""
    spec = build_spec_for_session(_session(runtime_session_id=None))
    assert spec.runtime_session_id is None


def test_runtime_session_id_empty_string_passes_through_as_none():
    """Empty-string is also falsy — the truthiness guard collapses it to
    ``None``. Pin so a mutant that swaps the guard for ``is not None`` is
    caught."""
    spec = build_spec_for_session(_session(runtime_session_id=""))
    assert spec.runtime_session_id is None


def test_runtime_session_id_uuid_is_stringified():
    """A UUID rehydrates to its string form (downstream consumers expect
    a plain string)."""
    rsid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    spec = build_spec_for_session(_session(runtime_session_id=rsid))
    assert spec.runtime_session_id == "12345678-1234-5678-1234-567812345678"
    assert isinstance(spec.runtime_session_id, str)


# ---------- pass-through fields ----------


def test_sprite_name_passes_through():
    spec = build_spec_for_session(_session(sprite_name="aod-abc123"))
    assert spec.name == "aod-abc123"


def test_user_passes_through_unchanged():
    """``session.user`` lands on ``spec.user`` as the same object — no
    copy, no rewrap."""
    user = SimpleNamespace(username="jake")
    spec = build_spec_for_session(_session(user=user))
    assert spec.user is user


def test_environment_passes_through_unchanged():
    """``session.environment`` lands on ``spec.environment`` as the same
    object — including ``None``."""
    env = SimpleNamespace(name="prod-env")
    spec = build_spec_for_session(_session(environment=env))
    assert spec.environment is env


def test_environment_none_passes_through_as_none():
    spec = build_spec_for_session(_session(environment=None))
    assert spec.environment is None


# ---------- runtime registry lookup ----------


def test_runtime_string_is_looked_up_in_registry():
    """``session.runtime`` (string) is used as a dict key into the global
    ``RUNTIMES`` registry; the looked-up Runtime instance lands on the
    spec. Pin against a mutant that hard-codes a single runtime or drops
    the lookup."""
    spec = build_spec_for_session(_session(runtime="claude"))
    assert spec.runtime is RUNTIMES["claude"]
