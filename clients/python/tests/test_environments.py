from __future__ import annotations

import pytest

from aod import ConflictError, Environment, Networking


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
    # Only set keys are sent
    assert "packages" not in sent
    assert "networking" not in sent


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
    server.json("DELETE", "/environments/abc/delete", 200, {"detail": "Environment deleted"})
    result = client.environments.delete("abc")
    assert result is None


def test_delete_with_sessions_conflicts(client, server):
    server.json(
        "DELETE",
        "/environments/abc/delete",
        409,
        {"detail": "Cannot delete environment with existing sessions"},
    )
    with pytest.raises(ConflictError):
        client.environments.delete("abc")


def test_versions_returns_history(client, server, make_environment):
    from datetime import datetime, timezone

    env = make_environment()
    history = [
        {
            "id": env["id"],
            "type": "environment",
            "name": "v1",
            "packages": {},
            "setup_script": None,
            "networking": {"type": "unrestricted"},
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    ]
    server.json("GET", f"/environments/{env['id']}/versions", 200, {"data": history})
    versions = client.environments.versions(env["id"])
    assert versions[0].version == 1
    assert versions[0].networking.type == "unrestricted"


def test_create_with_packages_and_networking(client, server, make_environment):
    env = make_environment()
    server.json("POST", "/environments", 201, env)

    client.environments.create(
        name="prod",
        packages={"apt": ["jq", "curl"], "npm": ["typescript"]},
        setup_script="echo hi",
        networking={"type": "limited", "allowed_hosts": ["api.github.com"]},
    )

    sent = server.requests[-1].body
    assert sent == {
        "name": "prod",
        "packages": {"apt": ["jq", "curl"], "npm": ["typescript"]},
        "setup_script": "echo hi",
        "networking": {"type": "limited", "allowed_hosts": ["api.github.com"]},
    }


def test_create_with_typed_networking(client, server, make_environment):
    server.json("POST", "/environments", 201, make_environment())
    client.environments.create(
        name="prod",
        networking=Networking(type="limited", allowed_hosts=["api.github.com"]),
    )
    assert server.requests[-1].body["networking"] == {
        "type": "limited",
        "allowed_hosts": ["api.github.com"],
    }


def test_networking_default_wire_format(client, server, make_environment):
    """`Networking()` with all defaults serializes to a complete payload —
    callers shouldn't have to guess what the server will see.
    """
    server.json("POST", "/environments", 201, make_environment())
    client.environments.create(name="prod", networking=Networking())
    assert server.requests[-1].body["networking"] == {
        "type": "unrestricted",
        "allowed_hosts": [],
    }


def test_typed_networking_limited_requires_hosts():
    """`Networking(type="limited")` with no hosts is a silent block-all
    on the server — fail at SDK construction instead.
    """
    with pytest.raises(ValueError, match="allowed_hosts must be non-empty"):
        Networking(type="limited")
    with pytest.raises(ValueError, match="allowed_hosts must be non-empty"):
        Networking(type="limited", allowed_hosts=[])


def test_typed_networking_defaults_to_unrestricted():
    """Default matches the server: env created without networking is unrestricted."""
    n = Networking()
    assert n.type == "unrestricted"
    assert n.allowed_hosts == []


def test_create_with_dict_networking_still_works(client, server, make_environment):
    server.json("POST", "/environments", 201, make_environment())
    client.environments.create(name="x", networking={"type": "unrestricted"})
    assert server.requests[-1].body["networking"] == {"type": "unrestricted"}


def test_update_accepts_typed_networking(client, server, make_environment):
    env = make_environment(version=2)
    server.json("PUT", f"/environments/{env['id']}", 200, env)
    client.environments.update(
        env["id"],
        version=1,
        networking=Networking(type="limited", allowed_hosts=["x.example"]),
    )
    assert server.requests[-1].body["networking"] == {
        "type": "limited",
        "allowed_hosts": ["x.example"],
    }
