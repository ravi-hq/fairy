from __future__ import annotations

import pydantic
import pytest

from aod import (
    Agent,
    AgentVersion,
    ConflictError,
    GithubSkill,
    InlineSkill,
    McpServer,
    McpServerStdio,
    McpServerUrl,
    NotFoundError,
    ValidationError,
)


def test_list(client, server, make_agent):
    a1, a2 = make_agent(name="one"), make_agent(name="two")
    server.json("GET", "/agents", 200, {"data": [a1, a2]})

    agents = client.agents.list()

    assert [a.name for a in agents] == ["one", "two"]
    assert all(isinstance(a, Agent) for a in agents)


def test_create_sends_optional_fields_only_when_set(client, server, make_agent):
    created = make_agent(name="created")
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="created",
        model="m",
        runtime="r",
        system="be helpful",
        description="demo agent",
        metadata={"k": "v"},
    )

    sent = server.requests[-1].body
    assert sent == {
        "name": "created",
        "model": "m",
        "runtime": "r",
        "system": "be helpful",
        "description": "demo agent",
        "metadata": {"k": "v"},
    }


def test_create_omits_null_optional_fields(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)
    client.agents.create(name="x", model="m", runtime="r")
    assert server.requests[-1].body == {"name": "x", "model": "m", "runtime": "r"}


def test_get(client, server, make_agent):
    agent = make_agent()
    server.json("GET", f"/agents/{agent['id']}", 200, agent)
    result = client.agents.get(agent["id"])
    assert result.id == agent["id"] or str(result.id) == agent["id"]


def test_get_404(client, server):
    server.json("GET", "/agents/missing", 404, {"detail": "Agent not found"})
    with pytest.raises(NotFoundError) as excinfo:
        client.agents.get("missing")
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Agent not found"


def test_update_sends_version_and_partial_fields(client, server, make_agent):
    updated = make_agent(version=2, name="renamed")
    server.json("PUT", f"/agents/{updated['id']}", 200, updated)

    client.agents.update(updated["id"], version=1, name="renamed")

    sent = server.requests[-1].body
    assert sent == {"version": 1, "name": "renamed"}


def test_update_stale_version_raises_conflict(client, server, make_agent):
    agent = make_agent()
    server.json("PUT", f"/agents/{agent['id']}", 409, {"detail": "Version mismatch"})
    with pytest.raises(ConflictError):
        client.agents.update(agent["id"], version=1, name="x")


def test_update_validation_error(client, server, make_agent):
    agent = make_agent()
    server.json(
        "PUT",
        f"/agents/{agent['id']}",
        422,
        {"detail": [{"loc": ["runtime"], "msg": "invalid"}]},
    )
    with pytest.raises(ValidationError) as excinfo:
        client.agents.update(agent["id"], version=1, runtime="bogus")
    assert isinstance(excinfo.value.detail, list)


def test_archive_returns_updated_agent(client, server, make_agent):
    from datetime import datetime, timezone

    agent = make_agent(archived_at=datetime.now(timezone.utc).isoformat())
    server.json("POST", f"/agents/{agent['id']}/archive", 200, agent)
    result = client.agents.archive(agent["id"])
    assert result.archived_at is not None


def test_archive_already_archived_conflict(client, server):
    server.json("POST", "/agents/abc/archive", 409, {"detail": "Agent is already archived"})
    with pytest.raises(ConflictError):
        client.agents.archive("abc")


def test_versions(client, server, make_agent):
    from datetime import datetime, timezone

    agent = make_agent()
    versions = [
        {
            "id": agent["id"],
            "type": "agent",
            "name": "v1",
            "description": None,
            "system": None,
            "model": "m",
            "runtime": "r",
            "environment_id": None,
            "skills": [],
            "mcp_servers": [],
            "metadata": {},
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    server.json("GET", f"/agents/{agent['id']}/versions", 200, {"data": versions})
    result = client.agents.versions(agent["id"])
    assert [v.name for v in result] == ["v1"]
    assert isinstance(result[0], AgentVersion)


def test_auth_header_sent(client, server, make_agent):
    server.json("GET", "/agents", 200, {"data": []})
    client.agents.list()
    assert server.requests[-1].headers["authorization"] == "Bearer aod_test"


def test_create_with_typed_skills(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        skills=[
            InlineSkill(name="careful", description="Be careful", content="rules go here"),
            GithubSkill(description="Browser automation", source="ravi-hq/skills", name="browse"),
            GithubSkill(description="Whole repo", source="ravi-hq/all-skills"),
        ],
    )

    assert server.requests[-1].body["skills"] == [
        {"name": "careful", "description": "Be careful", "content": "rules go here"},
        {
            "type": "github",
            "description": "Browser automation",
            "source": "ravi-hq/skills",
            "name": "browse",
        },
        {"type": "github", "description": "Whole repo", "source": "ravi-hq/all-skills"},
    ]


def test_create_with_dict_skills_still_works(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        skills=[{"name": "raw", "description": "d", "content": "c"}],
    )

    assert server.requests[-1].body["skills"] == [
        {"name": "raw", "description": "d", "content": "c"}
    ]


def test_inline_skill_rejects_bad_name():
    """Name regex matches the server's `SKILL_NAME_RE`."""
    with pytest.raises(pydantic.ValidationError):
        InlineSkill(name="Bad Name", description="d", content="c")
    with pytest.raises(pydantic.ValidationError):
        InlineSkill(name="-leading-dash", description="d", content="c")


def test_inline_skill_rejects_oversize_content():
    """Mirrors the server's MAX_SKILL_CONTENT_BYTES = 64 KiB cap."""
    too_big = "x" * (64 * 1024 + 1)
    with pytest.raises(pydantic.ValidationError, match="exceeds"):
        InlineSkill(name="x", description="d", content=too_big)


def test_inline_skill_rejects_heredoc_delimiter():
    """The server materializes content via a bash heredoc."""
    with pytest.raises(pydantic.ValidationError, match="SKILL_EOF"):
        InlineSkill(name="x", description="d", content="prefix SKILL_EOF suffix")


def test_github_skill_rejects_bad_source():
    """Mirrors the `owner/repo` regex on the server."""
    with pytest.raises(pydantic.ValidationError):
        GithubSkill(description="d", source="not-a-source")


def test_update_accepts_typed_skills(client, server, make_agent):
    agent = make_agent(version=2)
    server.json("PUT", f"/agents/{agent['id']}", 200, agent)

    client.agents.update(
        agent["id"],
        version=1,
        skills=[InlineSkill(name="s", description="d", content="c")],
    )

    assert server.requests[-1].body["skills"] == [
        {"name": "s", "description": "d", "content": "c"}
    ]


def test_create_with_typed_mcp_servers(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        mcp_servers=[
            McpServerUrl(name="docs", url="https://docs.example/mcp"),
            McpServerStdio(name="local", command="my-mcp-server", args=["--quiet"]),
        ],
    )

    assert server.requests[-1].body["mcp_servers"] == [
        {"name": "docs", "type": "url", "url": "https://docs.example/mcp"},
        {"name": "local", "type": "stdio", "command": "my-mcp-server", "args": ["--quiet"]},
    ]


def test_create_with_dict_mcp_servers_still_works(client, server, make_agent):
    """Plain dicts continue to work — typed inputs are additive."""
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        mcp_servers=[{"name": "raw", "type": "url", "url": "https://example/mcp"}],
    )

    assert server.requests[-1].body["mcp_servers"] == [
        {"name": "raw", "type": "url", "url": "https://example/mcp"}
    ]


def test_create_with_mixed_typed_and_dict_mcp_servers(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        mcp_servers=[
            McpServerUrl(name="a", url="https://a/mcp"),
            {"name": "b", "type": "stdio", "command": "b"},
        ],
    )

    assert server.requests[-1].body["mcp_servers"] == [
        {"name": "a", "type": "url", "url": "https://a/mcp"},
        {"name": "b", "type": "stdio", "command": "b"},
    ]


def test_typed_mcp_url_optional_headers(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        mcp_servers=[
            McpServerUrl(name="auth", url="https://api/mcp", headers={"X-Token": "secret"}),
        ],
    )

    sent = server.requests[-1].body["mcp_servers"][0]
    assert sent["headers"] == {"X-Token": "secret"}


def test_typed_mcp_stdio_optional_env(client, server, make_agent):
    created = make_agent()
    server.json("POST", "/agents", 201, created)

    client.agents.create(
        name="x",
        model="m",
        runtime="r",
        mcp_servers=[
            McpServerStdio(name="local", command="my-mcp-server", env={"K": "v"}),
        ],
    )

    sent = server.requests[-1].body["mcp_servers"][0]
    assert sent["env"] == {"K": "v"}


def test_update_accepts_typed_mcp_servers(client, server, make_agent):
    agent = make_agent(version=2)
    server.json("PUT", f"/agents/{agent['id']}", 200, agent)

    client.agents.update(
        agent["id"],
        version=1,
        mcp_servers=[McpServerStdio(name="s", command="cmd")],
    )

    assert server.requests[-1].body["mcp_servers"] == [
        {"name": "s", "type": "stdio", "command": "cmd"}
    ]


def test_mcp_server_response_exposes_optional_fields(client, server, make_agent):
    """Server-side responses include url/stdio-specific fields; SDK preserves them."""
    agent_payload = make_agent(
        mcp_servers=[
            {"name": "u", "type": "url", "url": "https://x/mcp", "headers": {"H": "v"}},
            {"name": "s", "type": "stdio", "command": "c", "args": ["a"], "env": {"E": "v"}},
        ]
    )
    server.json("GET", f"/agents/{agent_payload['id']}", 200, agent_payload)

    agent = client.agents.get(agent_payload["id"])
    assert isinstance(agent.mcp_servers[0], McpServer)
    assert agent.mcp_servers[0].headers == {"H": "v"}
    assert agent.mcp_servers[1].args == ["a"]
    assert agent.mcp_servers[1].env == {"E": "v"}
