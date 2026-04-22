from __future__ import annotations

import pytest

from aod import Agent, AgentVersion, ConflictError, NotFoundError, ValidationError


def test_list(client, server, make_agent):
    a1, a2 = make_agent(name="one"), make_agent(name="two")
    server.json("GET", "/agents", 200, {"data": [a1, a2]})

    agents = client.agents.list()

    assert [a.name for a in agents] == ["one", "two"]
    assert all(isinstance(a, Agent) for a in agents)


def test_create_sends_optional_fields_only_when_set(client, server, make_agent):
    created = make_agent(name="created")
    server.json("POST", "/agents", 201, created)

    client.agents.create(name="created", model="m", runtime="r", metadata={"k": "v"})

    sent = server.requests[-1].body
    assert sent == {"name": "created", "model": "m", "runtime": "r", "metadata": {"k": "v"}}


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
            "version": 1,
            "name": "v1",
            "model": "m",
            "runtime": "r",
            "system_prompt": None,
            "metadata": {},
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
