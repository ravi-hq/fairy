"""ClaudeRuntime behavior: build_command, write_config (MCP JSON at
/home/sprite/.claude.json), skills_root."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.claude import ClaudeRuntime
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


SESSION_UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="claudeuser", password="p")


def _spec(user) -> SessionSpec:
    return SessionSpec(
        name="sprite-x",
        runtime=ClaudeRuntime(),
        model="anthropic/claude-sonnet-4-6",
        user=user,
        runtime_session_id=SESSION_UUID,
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )


def test_skills_root():
    assert ClaudeRuntime().skills_root == "/home/sprite/.claude/skills"


def test_providers():
    assert ClaudeRuntime().providers == {"anthropic"}


def test_install_runs_claude_update():
    """Sprite base image pins claude to 2.1.92, which predates the
    Traces (beta) `TRACEPARENT` propagation. install() must run
    `claude update` so `claude_code.interaction` parents under our
    worker span. Delegating the version choice to `claude update` keeps
    the upgrade channel consistent with what claude itself ships.
    """
    sprite = RecordingSprite("s")
    ClaudeRuntime().install(sprite)
    assert len(sprite.commands) == 1
    argv = sprite.commands[0].argv
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    assert argv[2] == "claude update"


@pytest.mark.django_db
def test_build_command_run(user):
    argv = ClaudeRuntime().build_command(_spec(user), "run")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--session-id",
        SESSION_UUID,
    ]


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = ClaudeRuntime().build_command(_spec(user), "continue")
    assert argv == [
        "claude",
        "--dangerously-skip-permissions",
        "--print",
        "--verbose",
        "--output-format",
        "stream-json",
        "--resume",
        SESSION_UUID,
    ]


@pytest.mark.django_db
def test_write_config_url_server(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    ClaudeRuntime().write_config(
        sprite,
        spec,
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.claude.json"])
    assert cfg == {"mcpServers": {"github": {"type": "http", "url": "https://mcp.github.com/mcp"}}}


@pytest.mark.django_db
def test_write_config_url_server_with_headers(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    ClaudeRuntime().write_config(
        sprite,
        spec,
        [
            McpServerSpec(
                name="private",
                type="url",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer ${SECRET}"},
            )
        ],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.claude.json"])
    assert cfg["mcpServers"]["private"]["headers"] == {"Authorization": "Bearer ${SECRET}"}


@pytest.mark.django_db
def test_write_config_stdio_server(user):
    sprite = RecordingSprite("s")
    spec = _spec(user)
    ClaudeRuntime().write_config(
        sprite,
        spec,
        [
            McpServerSpec(
                name="local",
                type="stdio",
                command="npx",
                args=["-y", "@some/mcp-server"],
                env={"API_KEY": "val"},
            )
        ],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.claude.json"])
    assert cfg["mcpServers"]["local"] == {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@some/mcp-server"],
        "env": {"API_KEY": "val"},
    }


@pytest.mark.django_db
def test_write_config_empty_mcp_servers_writes_nothing(user):
    sprite = RecordingSprite("s")
    ClaudeRuntime().write_config(sprite, _spec(user), [])
    assert "/home/sprite/.claude.json" not in sprite.write_map()
