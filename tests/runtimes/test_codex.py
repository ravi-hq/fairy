"""CodexRuntime behavior: build_command, write_config (TOML at
/home/sprite/.codex/config.toml), skills_root."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.codex import CodexRuntime
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


@pytest.fixture
def user(db):
    return User.objects.create_user(username="codexuser", password="p")


def _spec(user) -> SessionSpec:
    return SessionSpec(
        name="sprite-x",
        runtime=CodexRuntime(),
        model="openai/gpt-4.1",
        user=user,
        runtime_session_id=None,
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )


def test_skills_root():
    assert CodexRuntime().skills_root == "/home/sprite/.codex/skills"


def test_providers():
    assert CodexRuntime().providers == {"openai"}


@pytest.mark.django_db
def test_build_command_run(user):
    argv = CodexRuntime().build_command(_spec(user), "run")
    assert argv == [
        "codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = CodexRuntime().build_command(_spec(user), "continue")
    assert argv == [
        "codex",
        "exec",
        "resume",
        "--last",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]


@pytest.mark.django_db
def test_write_config_url_server(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    CodexRuntime().write_config(
        sprite,
        spec,
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    toml = sprite.write_map()["/home/sprite/.codex/config.toml"]
    assert "[mcp_servers.github]" in toml
    assert 'url = "https://mcp.github.com/mcp"' in toml
    assert "required = true" in toml


@pytest.mark.django_db
def test_write_config_bearer_token_env_var(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    CodexRuntime().write_config(
        sprite,
        spec,
        [
            McpServerSpec(
                name="api",
                type="url",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer ${MY_TOKEN}"},
            )
        ],
    )
    toml = sprite.write_map()["/home/sprite/.codex/config.toml"]
    assert 'bearer_token_env_var = "MY_TOKEN"' in toml


@pytest.mark.django_db
def test_write_config_empty_mcp_servers_writes_nothing(user):
    sprite = RecordingSprite("s")
    CodexRuntime().write_config(sprite, _spec(user), [])
    assert "/home/sprite/.codex/config.toml" not in sprite.write_map()
