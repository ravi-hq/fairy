"""OpencodeRuntime behavior: install (npm), build_command (run + --continue),
write_config (JSON at /home/sprite/.config/opencode/opencode.json with
opencode's schema), skills_root."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.opencode import OPENCODE_VERSION, OpencodeRuntime
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


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
    assert OpencodeRuntime().skills_root == "/home/sprite/.config/opencode/skills"


def test_providers():
    assert OpencodeRuntime().providers == {"anthropic", "openai", "google"}


def test_install_runs_npm_global():
    sprite = RecordingSprite("s")
    OpencodeRuntime().install(sprite)
    assert len(sprite.commands) == 1
    argv = sprite.commands[0].argv
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    assert f"opencode-ai@{OPENCODE_VERSION}" in argv[2]
    assert "npm install -g" in argv[2]


@pytest.mark.django_db
def test_build_command_run(user):
    argv = OpencodeRuntime().build_command(_spec(user), "run")
    assert argv == [
        "opencode",
        "run",
        "--model",
        "anthropic/claude-haiku-4-5",
        "--format",
        "json",
    ]


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = OpencodeRuntime().build_command(_spec(user), "continue")
    assert argv == [
        "opencode",
        "run",
        "--model",
        "anthropic/claude-haiku-4-5",
        "--format",
        "json",
        "--continue",
    ]


@pytest.mark.django_db
def test_build_command_passes_through_provider_prefix(user):
    """Model string passes through unchanged — opencode takes provider/model_id."""
    argv = OpencodeRuntime().build_command(_spec(user, model="openai/gpt-4.1"), "run")
    assert argv[3] == "openai/gpt-4.1"


@pytest.mark.django_db
def test_write_config_url_server(user):
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.config/opencode/opencode.json"])
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
    cfg = json.loads(sprite.write_map()["/home/sprite/.config/opencode/opencode.json"])
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
    cfg = json.loads(sprite.write_map()["/home/sprite/.config/opencode/opencode.json"])
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
    assert "/home/sprite/.config/opencode/opencode.json" not in sprite.write_map()


@pytest.mark.django_db
def test_write_config_skips_unknown_type_server(user):
    """The API validator (`VALID_MCP_SERVER_TYPES`) blocks anything but
    `url`/`stdio` at request time, so this defensive `else: continue`
    branch only fires if a future spec drift introduces a new MCP type
    without an opencode builder. Pin the skip-don't-crash behavior so a
    refactor can't silently turn unknown types into `entry`-undefined
    NameError 500s.

    Mixed list — the unknown is skipped, the valid one is still written."""
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
        [
            McpServerSpec(name="ghost", type="future-shape"),
            McpServerSpec(name="real", type="url", url="https://example.com/mcp"),
        ],
    )
    cfg = json.loads(sprite.write_map()["/home/sprite/.config/opencode/opencode.json"])
    assert "ghost" not in cfg["mcp"]
    assert cfg["mcp"]["real"]["url"] == "https://example.com/mcp"


@pytest.mark.django_db
def test_write_config_all_unknown_types_writes_nothing(user):
    """Sibling case to the empty-list test: when *every* server is an
    unknown type, every iteration takes the `else: continue` branch,
    `config` stays empty, and the early `if not config: return` prevents
    the file from being written at all. Pins the file-not-written
    behavior so a refactor that drops the `if not config` guard would
    surface as a regression here rather than a stray empty-mcp file
    landing on every Sprite."""
    sprite = RecordingSprite("s")
    OpencodeRuntime().write_config(
        sprite,
        _spec(user),
        [
            McpServerSpec(name="ghost-1", type="future-shape"),
            McpServerSpec(name="ghost-2", type="another-future-shape"),
        ],
    )
    assert "/home/sprite/.config/opencode/opencode.json" not in sprite.write_map()
