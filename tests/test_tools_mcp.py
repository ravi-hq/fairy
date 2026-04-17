import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import Agent, AgentVersion, APIKey, UserRuntimeKey
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import McpServerSpec, _build_tool_flags, build_wrapper_script


# --- Fixtures ---


@pytest.fixture
def user(db):
    return User.objects.create_user(username="testuser", password="testpass")


@pytest.fixture
def api_key(user):
    instance, raw_key = APIKey.create_key(user, "test-key")
    return instance, raw_key


@pytest.fixture
def auth_headers(api_key):
    _, raw_key = api_key
    return {"HTTP_AUTHORIZATION": f"Bearer {raw_key}"}


@pytest.fixture
def runtime_key(user):
    urk = UserRuntimeKey(user=user, runtime="claude")
    urk.set_api_key("fake-anthropic-key")
    urk.save()
    return urk


@pytest.fixture
def mock_sprites(mocker):
    mock_sprite = mocker.MagicMock()
    mock_fs = mocker.MagicMock()
    mock_sprite.filesystem.return_value = mock_fs
    mock_fs.__truediv__ = mocker.Mock(return_value=mock_fs)
    mock_fs.write_text = mocker.Mock()
    mock_sprite.command.return_value.run = mocker.Mock()
    mock_client = mocker.MagicMock()
    mock_client.create_sprite.return_value = mock_sprite
    mocker.patch("fairy.views._get_client", return_value=mock_client)
    mocker.patch("fairy.views.threading.Thread")
    return mock_sprite, mock_fs


# --- Wrapper script MCP tests ---


class TestWrapperScriptMcp:
    def test_claude_mcp_config(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert "/tmp/mcp.json" in script
        assert '"mcpServers"' in script
        assert '"github"' in script
        assert "https://mcp.github.com/mcp" in script
        assert "--mcp-config /tmp/mcp.json" in script
        assert "--strict-mcp-config" in script

    def test_claude_mcp_with_headers(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(
                name="private",
                type="url",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer ${TOKEN}"},
            ),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert "Authorization" in script
        assert "Bearer ${TOKEN}" in script

    def test_claude_stdio_mcp(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(
                name="local",
                type="stdio",
                command="npx",
                args=["-y", "@some/mcp-server"],
                env={"API_KEY": "val"},
            ),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert '"stdio"' in script
        assert '"npx"' in script
        assert "@some/mcp-server" in script

    def test_codex_mcp_config(self):
        config = RUNTIMES["codex"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert "~/.codex/config.toml" in script
        assert "[mcp_servers.github]" in script
        assert 'url = "https://mcp.github.com/mcp"' in script
        # Codex doesn't need extra CLI flags
        assert "--mcp-config" not in script

    def test_codex_mcp_bearer_token_env_var(self):
        config = RUNTIMES["codex"]
        servers = [
            McpServerSpec(
                name="api",
                type="url",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer ${MY_TOKEN}"},
            ),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert 'bearer_token_env_var = "MY_TOKEN"' in script

    def test_gemini_mcp_config(self):
        config = RUNTIMES["gemini"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert "~/.gemini/settings.json" in script
        assert '"httpUrl"' in script
        assert "https://mcp.github.com/mcp" in script
        assert '"trust": true' in script

    def test_no_mcp_backward_compat(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello")
        assert "--mcp-config" not in script
        assert "mcp.json" not in script

    def test_multiple_mcp_servers(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
            McpServerSpec(name="slack", type="url", url="https://mcp.slack.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        assert '"github"' in script
        assert '"slack"' in script

    def test_mcp_section_before_exec(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="test", type="url", url="https://mcp.example.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", "hello", mcp_servers=servers)
        mcp_pos = script.index("MCP_EOF")
        exec_pos = script.index("exec ")
        assert mcp_pos < exec_pos


# --- Agent CRUD with tools/mcp_servers ---


@pytest.mark.django_db
class TestCreateAgentWithTools:
    def test_create_with_tools_and_mcp(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Tool Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "tools": [
                    {"type": "agent_toolset_20260401"},
                    {
                        "type": "mcp_toolset",
                        "mcp_server_name": "github",
                    },
                ],
                "mcp_servers": [
                    {
                        "type": "url",
                        "name": "github",
                        "url": "https://mcp.github.com/mcp",
                    },
                ],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["tools"]) == 2
        assert data["tools"][0]["type"] == "agent_toolset_20260401"
        assert data["tools"][1]["type"] == "mcp_toolset"
        assert len(data["mcp_servers"]) == 1
        assert data["mcp_servers"][0]["name"] == "github"

    def test_custom_tool_type_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Custom Tool Agent",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "tools": [
                    {
                        "type": "custom",
                        "name": "get_weather",
                        "description": "Get weather for a location",
                        "input_schema": {
                            "type": "object",
                            "properties": {"location": {"type": "string"}},
                            "required": ["location"],
                        },
                    },
                ],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "unknown type" in str(resp.json()["detail"]).lower()

    def test_create_without_tools_defaults_empty(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "No Tools",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["tools"] == []
        assert data["mcp_servers"] == []


@pytest.mark.django_db
class TestAgentToolsValidation:
    def test_invalid_tool_type(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "tools": [{"type": "invalid"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "unknown type" in str(resp.json()["detail"]).lower()

    def test_tool_missing_type(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "tools": [{"name": "foo"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "type" in str(resp.json()["detail"]).lower()

    def test_mcp_toolset_missing_server_name(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "tools": [{"type": "mcp_toolset"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "mcp_server_name" in str(resp.json()["detail"]).lower()


@pytest.mark.django_db
class TestAgentMcpValidation:
    def test_mcp_server_missing_name(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"type": "url", "url": "https://example.com/mcp"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "name" in str(resp.json()["detail"]).lower()

    def test_mcp_server_url_type_missing_url(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"type": "url", "name": "test"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "url" in str(resp.json()["detail"]).lower()

    def test_mcp_server_stdio_type_missing_command(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"type": "stdio", "name": "test"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "command" in str(resp.json()["detail"]).lower()

    def test_mcp_server_duplicate_names(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [
                    {"name": "github", "url": "https://a.com/mcp"},
                    {"name": "github", "url": "https://b.com/mcp"},
                ],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()["detail"]).lower()

    def test_mcp_server_invalid_type(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"type": "grpc", "name": "test", "url": "https://a.com"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "unknown type" in str(resp.json()["detail"]).lower()

    def test_mcp_server_max_20(self, client: Client, auth_headers):
        servers = [
            {"name": f"server-{i}", "url": f"https://s{i}.com/mcp"}
            for i in range(21)
        ]
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": servers,
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "20" in str(resp.json()["detail"])


@pytest.mark.django_db
class TestUpdateAgentTools:
    def test_update_tools(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
        )
        AgentVersion.objects.create(
            agent=agent, version=1, name=agent.name,
            model=agent.model, runtime=agent.runtime,
        )

        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps({
                "version": 1,
                "tools": [{"type": "agent_toolset_20260401"}],
                "mcp_servers": [
                    {"name": "github", "url": "https://mcp.github.com/mcp"},
                ],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert len(data["tools"]) == 1
        assert len(data["mcp_servers"]) == 1

    def test_update_tools_versioned(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
        )
        AgentVersion.objects.create(
            agent=agent, version=1, name=agent.name,
            model=agent.model, runtime=agent.runtime,
        )

        client.put(
            f"/agents/{agent.id}",
            data=json.dumps({
                "version": 1,
                "mcp_servers": [{"name": "s1", "url": "https://a.com/mcp"}],
            }),
            content_type="application/json",
            **auth_headers,
        )

        resp = client.get(f"/agents/{agent.id}/versions", **auth_headers)
        versions = resp.json()["data"]
        assert len(versions) == 2
        assert versions[0]["mcp_servers"] == [{"name": "s1", "url": "https://a.com/mcp"}]
        assert versions[1]["mcp_servers"] == []


# --- Session + MCP integration ---


@pytest.mark.django_db
class TestSessionMcpIntegration:
    def test_session_with_agent_mcp_servers(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user, name="MCP Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
            mcp_servers=[
                {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"},
            ],
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        written_script = mock_fs.write_text.call_args[0][0]
        assert "--mcp-config" in written_script
        assert "github" in written_script
        assert "mcp.github.com" in written_script

    def test_session_agent_without_mcp_no_config(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user, name="Plain Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        written_script = mock_fs.write_text.call_args[0][0]
        assert "--mcp-config" not in written_script

    def test_session_mcp_with_headers_in_config(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user, name="Auth Agent", model="claude-sonnet-4-6",
            runtime="claude", version=1,
            mcp_servers=[
                {
                    "type": "url",
                    "name": "private",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer ${SECRET}"},
                },
            ],
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        written_script = mock_fs.write_text.call_args[0][0]
        assert "Authorization" in written_script
        assert "Bearer ${SECRET}" in written_script


# --- Wrapper script tool-enforcement wiring (Phase 1 — scaffolding only) ---


class TestWrapperScriptToolPlumbing:
    @pytest.mark.parametrize("runtime_name", ["claude", "claude-oauth", "codex", "gemini"])
    def test_build_tool_flags_empty_returns_empty(self, runtime_name):
        flags, files = _build_tool_flags(runtime_name, [], [])
        assert flags == ""
        assert files == {}

    @pytest.mark.parametrize("runtime_name", ["claude", "claude-oauth", "codex", "gemini"])
    def test_wrapper_script_accepts_tools_kwarg(self, runtime_name):
        config = RUNTIMES[runtime_name]
        tools = [{"type": "agent_toolset_20260401"}]
        script = build_wrapper_script(config, "sk-test", "hello", tools=tools)
        assert script.startswith("#!/bin/bash")

    def test_wrapper_script_tools_default_is_none(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello")
        assert "exec claude" in script
        # No tool flags emitted when no tools passed
        assert "--tools" not in script
        assert "--disallowedTools" not in script

    def test_wrapper_script_empty_tools_no_flags(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test", "hello", tools=[])
        assert "--tools" not in script
        assert "--disallowedTools" not in script


# --- Phase 2: Claude tool flag translation ---


class TestClaudeToolFlags:
    def _flags(self, tools, mcp_refs=None):
        from fairy.sprites_exec import _tool_flags_claude
        return _tool_flags_claude(tools, mcp_refs or [])

    def test_empty_returns_empty(self):
        assert self._flags([]) == ""

    def test_only_mcp_toolset_no_agent_toolset_returns_empty(self):
        """mcp_toolset alone doesn't restrict built-ins; needs an agent_toolset."""
        tools = [{"type": "mcp_toolset", "mcp_server_name": "github"}]
        assert self._flags(tools, ["github"]) == ""

    def test_default_enabled_true_no_overrides_returns_empty(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": True}}]
        assert self._flags(tools) == ""

    def test_default_enabled_missing_treated_as_true(self):
        """Absent default_config defaults to enabled=True (no flags)."""
        tools = [{"type": "agent_toolset_20260401"}]
        assert self._flags(tools) == ""

    def test_default_enabled_false_no_overrides_disables_all(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        assert self._flags(tools) == ' --tools ""'

    def test_default_enabled_false_with_allowlist(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [
                {"name": "bash", "enabled": True},
                {"name": "read", "enabled": True},
            ],
        }]
        # Order follows _CLAUDE_TOOL_NAMES insertion order (bash, read first)
        assert self._flags(tools) == ' --tools "Bash,Read"'

    def test_default_enabled_true_with_denylist(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "web_fetch", "enabled": False},
                {"name": "web_search", "enabled": False},
            ],
        }]
        flags = self._flags(tools)
        # Order matches configs input order
        assert flags == ' --disallowedTools "WebFetch,WebSearch"'

    @pytest.mark.parametrize("canonical,pascal", [
        ("bash", "Bash"), ("read", "Read"), ("write", "Write"),
        ("edit", "Edit"), ("glob", "Glob"), ("grep", "Grep"),
        ("web_fetch", "WebFetch"), ("web_search", "WebSearch"),
    ])
    def test_every_canonical_name_maps_correctly(self, canonical, pascal):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": canonical, "enabled": False}],
        }]
        assert self._flags(tools) == f' --disallowedTools "{pascal}"'

    def test_mcp_toolset_included_in_allowlist(self):
        tools = [
            {"type": "agent_toolset_20260401", "default_config": {"enabled": False}},
            {"type": "mcp_toolset", "mcp_server_name": "slack"},
        ]
        assert self._flags(tools, ["slack"]) == ' --tools "mcp__slack"'

    def test_mcp_toolset_combined_with_built_in_allowlist(self):
        tools = [
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": False},
                "configs": [{"name": "read", "enabled": True}],
            },
            {"type": "mcp_toolset", "mcp_server_name": "github"},
        ]
        assert self._flags(tools, ["github"]) == ' --tools "Read,mcp__github"'

    def test_unknown_tool_name_in_configs_is_ignored(self):
        """If user passes a name not in the canonical map, skip it."""
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "not_a_real_tool", "enabled": False}],
        }]
        assert self._flags(tools) == ""

    def test_wrapper_script_claude_includes_tool_flags(self):
        config = RUNTIMES["claude"]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "web_fetch", "enabled": False}],
        }]
        script = build_wrapper_script(config, "key", "prompt", tools=tools)
        assert '--disallowedTools "WebFetch"' in script

    def test_wrapper_script_claude_oauth_includes_tool_flags(self):
        config = RUNTIMES["claude-oauth"]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
        }]
        script = build_wrapper_script(config, "key", "prompt", tools=tools)
        assert '--tools ""' in script

    def test_claude_continue_session_includes_tool_flags(self):
        """Restrictions don't persist on --continue — must re-emit flags."""
        config = RUNTIMES["claude"]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "bash", "enabled": False}],
        }]
        script = build_wrapper_script(
            config, "key", "prompt", continue_session=True, tools=tools
        )
        assert '--disallowedTools "Bash"' in script
        assert "--continue" in script

