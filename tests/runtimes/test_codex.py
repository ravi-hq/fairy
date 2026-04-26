"""CodexRuntime behavior: build_command, write_config (TOML at
/home/sprite/.codex/config.toml), skills_root."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User

from agent_on_demand.runtimes.codex import CodexRuntime
from agent_on_demand.session_service.errors import ProvisionError
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


def test_install_is_a_no_op():
    """The Codex CLI is preinstalled in the runtime image, so .install() must
    do nothing — adding work here would silently slow every session start."""
    assert CodexRuntime().install(sprite=None) is None


@pytest.mark.django_db
def test_write_config_stdio_server_writes_command_args_env(user):
    """stdio MCP servers are the most common type — confirm command, args
    list, and env block all land in the TOML output."""
    sprite = RecordingSprite("s")
    spec = _spec(user)
    CodexRuntime().write_config(
        sprite,
        spec,
        [
            McpServerSpec(
                name="local",
                type="stdio",
                command="npx",
                args=["-y", "@modelcontextprotocol/server-everything"],
                env={"DEBUG": "1", "API_BASE": "https://x"},
            )
        ],
    )
    toml = sprite.write_map()["/home/sprite/.codex/config.toml"]
    assert "[mcp_servers.local]" in toml
    assert 'command = "npx"' in toml
    assert 'args = ["-y", "@modelcontextprotocol/server-everything"]' in toml
    assert "[mcp_servers.local.env]" in toml
    assert 'DEBUG = "1"' in toml
    assert 'API_BASE = "https://x"' in toml


@pytest.mark.django_db
def test_write_config_stdio_without_args_or_env(user):
    """Optional fields stay out of the output when not provided."""
    sprite = RecordingSprite("s")
    CodexRuntime().write_config(
        sprite,
        _spec(user),
        [McpServerSpec(name="bare", type="stdio", command="run-mcp")],
    )
    toml = sprite.write_map()["/home/sprite/.codex/config.toml"]
    assert 'command = "run-mcp"' in toml
    assert "args = " not in toml
    assert "[mcp_servers.bare.env]" not in toml


@pytest.mark.django_db
def test_write_config_literal_bearer_token_raises(user):
    """Codex's TOML schema only supports `bearer_token_env_var`; a literal
    `Bearer <secret>` value would otherwise be silently dropped or worse,
    written verbatim into the config. Reject loudly at provisioning."""
    sprite = RecordingSprite("s")
    with pytest.raises(ProvisionError) as exc_info:
        CodexRuntime().write_config(
            sprite,
            _spec(user),
            [
                McpServerSpec(
                    name="api",
                    type="url",
                    url="https://mcp.example.com/mcp",
                    headers={"Authorization": "Bearer literal-secret-value"},
                )
            ],
        )
    assert exc_info.value.stage == "write_config"
    assert "literal value" in str(exc_info.value)


@pytest.mark.django_db
def test_write_config_non_authorization_header_raises(user):
    """Codex's MCP config supports exactly one header form
    (`Authorization: Bearer ${ENV}`). Reject anything else loudly so a
    user-supplied custom header doesn't get silently dropped."""
    sprite = RecordingSprite("s")
    with pytest.raises(ProvisionError) as exc_info:
        CodexRuntime().write_config(
            sprite,
            _spec(user),
            [
                McpServerSpec(
                    name="api",
                    type="url",
                    url="https://mcp.example.com/mcp",
                    headers={"X-Custom-Header": "some-value"},
                )
            ],
        )
    assert exc_info.value.stage == "write_config"
    assert "X-Custom-Header" in str(exc_info.value)
