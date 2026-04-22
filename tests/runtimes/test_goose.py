"""GooseRuntime behavior: install (apt+curl), build_command (run + --resume),
write_config (YAML at /home/sprite/.config/goose/config.yaml with goose's
schema), skills_root is None."""

from __future__ import annotations

import yaml

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.goose import GOOSE_VERSION, GooseRuntime
from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec
from tests.fakes.sprite import RecordingSprite


@pytest.fixture
def user(db):
    return User.objects.create_user(username="gooseuser", password="p")


def _spec(user, model: str = "anthropic/claude-haiku-4-5") -> SessionSpec:
    return SessionSpec(
        name="sprite-x",
        runtime=GooseRuntime(),
        model=model,
        user=user,
        runtime_session_id="sess-abc",
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )


def test_skills_root_is_none():
    assert GooseRuntime().skills_root is None


def test_providers():
    assert GooseRuntime().providers == frozenset({"anthropic", "openai", "google"})


def test_install_runs_apt_and_curl():
    sprite = RecordingSprite("s")
    GooseRuntime().install(sprite)
    assert len(sprite.commands) == 1
    argv = sprite.commands[0].argv
    assert argv[0] == "bash"
    assert argv[1] == "-lc"
    assert GOOSE_VERSION in argv[2]
    assert "download_cli.sh" in argv[2]
    assert "CONFIGURE=false" in argv[2]


@pytest.mark.django_db
def test_build_command_run(user):
    argv = GooseRuntime().build_command(_spec(user), "run")
    assert argv[0:2] == ["goose", "run"]
    assert "--instructions" in argv
    assert "-" in argv
    assert "--mode" in argv
    assert "auto" in argv
    assert "--provider" in argv
    assert "anthropic" in argv
    assert "--model" in argv
    assert "claude-haiku-4-5" in argv
    assert "--resume" not in argv


@pytest.mark.django_db
def test_build_command_continue(user):
    argv = GooseRuntime().build_command(_spec(user), "continue")
    assert "--resume" in argv


@pytest.mark.django_db
def test_build_command_splits_provider_and_model(user):
    argv = GooseRuntime().build_command(_spec(user, model="openai/gpt-4.1"), "run")
    provider_idx = argv.index("--provider")
    model_idx = argv.index("--model")
    assert argv[provider_idx + 1] == "openai"
    assert argv[model_idx + 1] == "gpt-4.1"


@pytest.mark.django_db
def test_build_command_uses_runtime_session_id(user):
    spec = SessionSpec(
        name="sprite-x",
        runtime=GooseRuntime(),
        model="anthropic/claude-haiku-4-5",
        user=user,
        runtime_session_id="my-session-42",
        environment=None,
        repos=[],
        mcp_servers=[],
        skills=[],
    )
    argv = GooseRuntime().build_command(spec, "run")
    name_idx = argv.index("--name")
    assert argv[name_idx + 1] == "my-session-42"


@pytest.mark.django_db
def test_write_config_creates_dir_and_writes_yaml(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(sprite, _spec(user), [])
    # mkdir command should have been recorded
    assert any("mkdir" in " ".join(c.argv) for c in sprite.commands)
    # config file should be written
    assert "/home/sprite/.config/goose/config.yaml" in sprite.write_map()


@pytest.mark.django_db
def test_write_config_contains_provider_model_keyring(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(sprite, _spec(user), [])
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    assert cfg["GOOSE_PROVIDER"] == "anthropic"
    assert cfg["GOOSE_MODEL"] == "claude-haiku-4-5"
    assert cfg["GOOSE_MODE"] == "auto"
    assert cfg["GOOSE_DISABLE_KEYRING"] is True
    assert cfg["GOOSE_TELEMETRY_ENABLED"] is False


@pytest.mark.django_db
def test_write_config_includes_developer_extension(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(sprite, _spec(user), [])
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    assert "developer" in cfg["extensions"]
    dev = cfg["extensions"]["developer"]
    assert dev["type"] == "builtin"
    assert dev["enabled"] is True


@pytest.mark.django_db
def test_write_config_url_mcp_server(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(
        sprite,
        _spec(user),
        [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")],
    )
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    ext = cfg["extensions"]["github"]
    assert ext["type"] == "streamable_http"
    assert ext["uri"] == "https://mcp.github.com/mcp"
    assert ext["enabled"] is True
    assert "headers" not in ext


@pytest.mark.django_db
def test_write_config_url_mcp_server_with_headers(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(
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
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    assert cfg["extensions"]["private"]["headers"] == {"Authorization": "Bearer ${SECRET}"}


@pytest.mark.django_db
def test_write_config_stdio_mcp_server(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(
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
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    ext = cfg["extensions"]["local"]
    assert ext["type"] == "stdio"
    assert ext["cmd"] == "npx"
    assert ext["args"] == ["-y", "@some/mcp-server"]
    assert ext["envs"] == {"API_KEY": "val"}
    assert ext["enabled"] is True
    assert ext["timeout"] == 300


@pytest.mark.django_db
def test_write_config_multiple_mcp_servers(user):
    sprite = RecordingSprite("s")
    GooseRuntime().write_config(
        sprite,
        _spec(user),
        [
            McpServerSpec(name="srv1", type="url", url="https://a.example.com/mcp"),
            McpServerSpec(name="srv2", type="url", url="https://b.example.com/mcp"),
        ],
    )
    cfg = yaml.safe_load(sprite.write_map()["/home/sprite/.config/goose/config.yaml"])
    assert "srv1" in cfg["extensions"]
    assert "srv2" in cfg["extensions"]
