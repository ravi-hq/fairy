"""OpencodeRuntime behavior: install (npm + symlink + pinned HOME),
build_command (cd + HOME wrapper, run + --continue), write_config (JSON
under the pinned HOME), skills_root."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.opencode import (
    OPENCODE_CONFIG_DIR,
    OPENCODE_HOME,
    OPENCODE_VERSION,
    OpencodeRuntime,
)
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


CONFIG_PATH = f"{OPENCODE_CONFIG_DIR}/opencode.json"


@pytest.fixture
def user(db):
    return User.objects.create_user(username="opencodeuser", password="p")


def _spec(user, model: str = "anthropic/claude-haiku-4-5") -> SessionSpec:
    return SessionSpec(
        name="sprite-x",
        runtime=OpencodeRuntime(),
        model=model,
        user=user,
        runtime_session_id=None,
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )


def test_skills_root():
    assert OpencodeRuntime().skills_root == f"{OPENCODE_CONFIG_DIR}/skills"


def test_providers():
    assert OpencodeRuntime().providers == {"anthropic", "openai", "google"}


def test_install_runs_npm_global():
    sprite = RecordingSprite("s")
    OpencodeRuntime().install(sprite)
    assert len(sprite.commands) == 1
    argv = sprite.commands[0].argv
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    script = argv[2]
    assert f"opencode-ai@{OPENCODE_VERSION}" in script
    assert "npm install -g" in script
    # Symlink into a PATH-visible location (nvm prefix bin isn't on PATH).
    assert "/home/sprite/.local/bin/opencode" in script
    # Pre-create the pinned HOME's config dir so write_config + per-turn
    # cd both succeed.
    assert OPENCODE_CONFIG_DIR in script


@pytest.mark.django_db
def test_build_command_run(user):
    argv = OpencodeRuntime().build_command(_spec(user), "run")
    # bash -c wrapper that pins cwd + HOME outside /home/sprite.
    assert argv[0] == "bash"
    assert argv[1] == "-c"
    script = argv[2]
    assert script.startswith(f"cd {OPENCODE_HOME} && ")
    assert f"exec env HOME={OPENCODE_HOME}" in script
    assert "opencode run --model anthropic/claude-haiku-4-5 --format json" in script
    assert "--continue" not in script


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = OpencodeRuntime().build_command(_spec(user), "continue")
    assert argv[0] == "bash"
    assert argv[1] == "-c"
    assert argv[2].endswith(" --continue")


@pytest.mark.django_db
def test_build_command_passes_through_provider_prefix(user):
    """Model string passes through unchanged — opencode takes provider/model_id."""
    argv = OpencodeRuntime().build_command(_spec(user, model="openai/gpt-4.1"), "run")
    assert "--model openai/gpt-4.1" in argv[2]


@pytest.mark.django_db
def test_write_config_url_server(user):
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    cfg = json.loads(sprite.write_map()[CONFIG_PATH])
    assert cfg == {
        "mcp": {
            "github": {
                "type": "remote",
                "url": "https://mcp.github.com/mcp",
                "enabled": True,
            }
        }
    }


@pytest.mark.django_db
def test_write_config_url_server_with_headers(user):
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
        [
            McpServerSpec(
                name="private",
                type="url",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer ${SECRET}"},
            )
        ],
    )
    cfg = json.loads(sprite.write_map()[CONFIG_PATH])
    assert cfg["mcp"]["private"]["headers"] == {"Authorization": "Bearer ${SECRET}"}


@pytest.mark.django_db
def test_write_config_stdio_server(user):
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
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
    cfg = json.loads(sprite.write_map()[CONFIG_PATH])
    # Opencode quirks: `command` is a single combined array (not command+args
    # split), env key is `environment`, type is `local` (not `stdio`).
    assert cfg["mcp"]["local"] == {
        "type": "local",
        "command": ["npx", "-y", "@some/mcp-server"],
        "enabled": True,
        "environment": {"API_KEY": "val"},
    }


@pytest.mark.django_db
def test_write_config_empty_mcp_servers_writes_nothing(user):
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(sprite, _spec(user), [])
    assert CONFIG_PATH not in sprite.write_map()
