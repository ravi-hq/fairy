from __future__ import annotations

import pytest

from aod import ConflictError, Environment


def test_list(client, server, make_environment):
    env = make_environment()
    server.json("GET", "/environments", 200, {"data": [env]})
    result = client.environments.list()
    assert len(result) == 1
    assert isinstance(result[0], Environment)


def test_create_with_env_vars(client, server, make_environment):
    env = make_environment()
    server.json("POST", "/environments", 201, env)

    client.environments.create(name="prod", env_vars={"OPENAI_API_KEY": "sk-xxx"})

    sent = server.requests[-1].body
    assert sent == {"name": "prod", "env_vars": {"OPENAI_API_KEY": "sk-xxx"}}


def test_update_optimistic_concurrency(client, server, make_environment):
    env = make_environment()
    server.json("PUT", f"/environments/{env['id']}", 200, env)

    client.environments.update(env["id"], version=env["version"], name="renamed")

    sent = server.requests[-1].body
    assert sent["version"] == env["version"]
    assert sent["name"] == "renamed"
    assert "resources" not in sent  # only set keys sent


def test_archive(client, server, make_environment):
    from datetime import datetime, timezone

    env = make_environment(archived_at=datetime.now(timezone.utc).isoformat())
    server.json("POST", f"/environments/{env['id']}/archive", 200, env)
    result = client.environments.archive(env["id"])
    assert result.archived_at is not None


def test_archive_already_archived(client, server):
    server.json(
        "POST",
        "/environments/abc/archive",
        409,
        {"detail": "Environment is already archived"},
    )
    with pytest.raises(ConflictError):
        client.environments.archive("abc")


def test_delete_returns_none(client, server):
    server.json("DELETE", "/environments/abc/delete", 204, None)
    result = client.environments.delete("abc")
    assert result is None


def test_versions_returns_history(client, server, make_environment):
    from datetime import datetime, timezone

    env = make_environment()
    history = [
        {
            "version": 1,
            "name": "v1",
            "resources": [],
            "setup_commands": [],
            "network_policy": None,
            "metadata": {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    server.json("GET", f"/environments/{env['id']}/versions", 200, {"data": history})
    versions = client.environments.versions(env["id"])
    assert versions[0].version == 1
