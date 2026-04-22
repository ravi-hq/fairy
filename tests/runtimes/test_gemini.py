"""GeminiRuntime behavior: build_command, write_config (JSON at
/home/sprite/.gemini/settings.json), skills_root."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.gemini import GeminiRuntime
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


@pytest.fixture
def user(db):
    return User.objects.create_user(username="geminiuser", password="p")


def _spec(user) -> SessionSpec:
    return SessionSpec(
        name="sprite-x",
        runtime=GeminiRuntime(),
        model="google/gemini-2.5-pro",
        user=user,
        runtime_session_id=None,
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )


def test_skills_root():
    assert GeminiRuntime().skills_root == "/home/sprite/.gemini/skills"


def test_providers():
    assert GeminiRuntime().providers == {"google"}


@pytest.mark.django_db
def test_build_command_run(user):
    argv = GeminiRuntime().build_command(_spec(user), "run")
    assert argv == ["gemini", "--output-format", "stream-json"]


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = GeminiRuntime().build_command(_spec(user), "continue")
    assert argv == ["gemini", "--resume", "--output-format", "stream-json"]


@pytest.mark.django_db
def test_write_config_url_server(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    GeminiRuntime().write_config(
        sprite,
        spec,
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.gemini/settings.json"])
    assert cfg["mcpServers"]["github"]["httpUrl"] == "https://mcp.github.com/mcp"
    assert cfg["mcpServers"]["github"]["trust"] is True


@pytest.mark.django_db
def test_write_config_stdio_server(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    GeminiRuntime().write_config(
        sprite,
        spec,
        [
            McpServerSpec(
                name="local",
                type="stdio",
                command="npx",
                args=["-y", "@some/mcp-server"],
            )
        ],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.gemini/settings.json"])
    assert cfg["mcpServers"]["local"]["command"] == "npx"
    assert cfg["mcpServers"]["local"]["trust"] is True


@pytest.mark.django_db
def test_write_config_empty_mcp_servers_writes_nothing(user):
    sprite = RecordingSprite("s")
    GeminiRuntime().write_config(sprite, _spec(user), [])
    assert "/home/sprite/.gemini/settings.json" not in sprite.write_map()
