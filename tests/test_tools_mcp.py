import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from fairy.models import Agent, AgentVersion, APIKey, UserRuntimeKey
from fairy.runtimes import RUNTIMES
from fairy.sprites_exec import (
    CLAUDE_SETTINGS_PATH,
    McpServerSpec,
    McpToolsetRules,
    _build_mcp_toolset_rules,
    _build_tool_flags,
    _tool_files_claude_mcp,
    build_wrapper_script,
)


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


# --- Phase 3: Gemini Policy Engine TOML ---


class TestGeminiToolFiles:
    def _files(self, tools):
        from fairy.sprites_exec import _tool_files_gemini
        return _tool_files_gemini(tools)

    def test_no_toolset_returns_empty(self):
        assert self._files([]) == {}

    def test_default_enabled_no_overrides_returns_empty(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": True}}]
        assert self._files(tools) == {}

    def test_default_off_returns_deny_all(self):
        from fairy.sprites_exec import GEMINI_POLICY_PATH
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        content = self._files(tools)[GEMINI_POLICY_PATH]
        assert 'toolName = "*"' in content
        assert 'decision = "deny"' in content

    def test_default_off_with_allowlist(self):
        from fairy.sprites_exec import GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
            "configs": [{"name": "bash", "enabled": True}],
        }]
        content = self._files(tools)[GEMINI_POLICY_PATH]
        assert 'toolName = "*"' in content
        assert '"run_shell_command"' in content
        assert 'decision = "allow"' in content

    def test_write_disabled_denies_both_gemini_tools(self):
        """Managed-Agents `write` must deny both `write_file` AND `replace`."""
        from fairy.sprites_exec import GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "write", "enabled": False}],
        }]
        content = self._files(tools)[GEMINI_POLICY_PATH]
        assert '"write_file"' in content
        assert '"replace"' in content
        assert 'decision = "deny"' in content

    @pytest.mark.parametrize("canonical,expected", [
        ("bash", ["run_shell_command"]),
        ("read", ["read_file"]),
        ("edit", ["replace"]),
        ("glob", ["glob"]),
        ("grep", ["grep_search"]),
        ("web_fetch", ["web_fetch"]),
        ("web_search", ["google_web_search"]),
    ])
    def test_every_canonical_name_maps_correctly(self, canonical, expected):
        from fairy.sprites_exec import GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": canonical, "enabled": False}],
        }]
        content = self._files(tools)[GEMINI_POLICY_PATH]
        for name in expected:
            assert f'"{name}"' in content

    def test_interactive_false_on_every_rule(self):
        from fairy.sprites_exec import GEMINI_POLICY_PATH
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "bash", "enabled": False}],
        }]
        content = self._files(tools)[GEMINI_POLICY_PATH]
        assert "interactive = false" in content

    def test_wrapper_script_gemini_writes_policy_file(self):
        config = RUNTIMES["gemini"]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "bash", "enabled": False}],
        }]
        script = build_wrapper_script(config, "key", "prompt", tools=tools)
        assert "/home/sprite/.gemini/policies/fairy.toml" in script
        assert "TOOLFILE_EOF" in script
        assert '"run_shell_command"' in script

    def test_gemini_no_tool_flags_on_exec_line(self):
        """Gemini enforcement is file-based; no CLI flags appended to exec."""
        config = RUNTIMES["gemini"]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "bash", "enabled": False}],
        }]
        script = build_wrapper_script(config, "key", "prompt", tools=tools)
        # Extract the exec line
        exec_line = next(line for line in script.splitlines() if line.startswith("exec "))
        assert "--tools" not in exec_line
        assert "--disallowedTools" not in exec_line


# --- Phase 4: Codex config.toml tool enforcement ---


class TestCodexToolConfig:
    def _script(self, tools, servers=None):
        config = RUNTIMES["codex"]
        return build_wrapper_script(
            config, "key", "prompt", mcp_servers=servers, tools=tools,
        )

    def test_no_tools_no_mcp_no_config_block(self):
        script = self._script([])
        assert "~/.codex/config.toml" not in script

    def test_web_search_disabled_writes_top_level_key(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "web_search", "enabled": False}],
        }]
        script = self._script(tools)
        assert 'web_search = "disabled"' in script

    def test_write_and_edit_disabled_sets_read_only_sandbox(self):
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "write", "enabled": False},
                {"name": "edit", "enabled": False},
            ],
        }]
        script = self._script(tools)
        assert 'sandbox_mode = "read-only"' in script

    def test_only_write_disabled_does_not_set_sandbox(self):
        """Sandbox is blunt — only flip it if both write AND edit are disabled."""
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "write", "enabled": False}],
        }]
        script = self._script(tools)
        assert "sandbox_mode" not in script

    def test_default_off_disables_web_search_and_sets_sandbox(self):
        tools = [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]
        script = self._script(tools)
        assert 'web_search = "disabled"' in script
        assert 'sandbox_mode = "read-only"' in script

    def test_unenforceable_tools_are_silent(self):
        """bash/read/glob/grep/web_fetch disable produces no codex config."""
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "bash", "enabled": False},
                {"name": "read", "enabled": False},
                {"name": "glob", "enabled": False},
                {"name": "grep", "enabled": False},
                {"name": "web_fetch", "enabled": False},
            ],
        }]
        script = self._script(tools)
        # None of these produce a top-level key, and no MCP servers either
        assert "~/.codex/config.toml" not in script

    def test_mcp_still_works_alongside_tool_config(self):
        servers = [McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp")]
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": True},
            "configs": [{"name": "web_search", "enabled": False}],
        }]
        script = self._script(tools, servers=servers)
        assert 'web_search = "disabled"' in script
        assert "[mcp_servers.github]" in script

    def test_codex_no_tool_flags_on_exec_line(self):
        """Codex enforcement is file-based; no CLI flags appended to exec."""
        tools = [{
            "type": "agent_toolset_20260401",
            "default_config": {"enabled": False},
        }]
        script = self._script(tools)
        exec_line = next(line for line in script.splitlines() if line.startswith("exec "))
        assert "--tools" not in exec_line
        assert "--disallowedTools" not in exec_line
# --- MCP toolset rules helper ---


class TestBuildMcpToolsetRules:
    def test_empty_tools_returns_empty(self):
        assert _build_mcp_toolset_rules([]) == {}

    def test_no_mcp_toolset_returns_empty(self):
        tools = [{"type": "agent_toolset_20260401"}]
        assert _build_mcp_toolset_rules(tools) == {}

    def test_bare_mcp_toolset_keeps_default_allow(self):
        tools = [{"type": "mcp_toolset", "mcp_server_name": "github"}]
        rules = _build_mcp_toolset_rules(tools)
        assert rules == {"github": McpToolsetRules(default_enabled=True, per_tool={})}

    def test_default_config_disabled(self):
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "github",
            "default_config": {"enabled": False},
        }]
        rules = _build_mcp_toolset_rules(tools)
        assert rules["github"].default_enabled is False
        assert rules["github"].per_tool == {}

    def test_per_tool_configs_override_default(self):
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "github",
            "default_config": {"enabled": True},
            "configs": [
                {"name": "create_issue", "enabled": False},
                {"name": "list_issues", "enabled": True},
            ],
        }]
        rules = _build_mcp_toolset_rules(tools)
        assert rules["github"].default_enabled is True
        assert rules["github"].per_tool == {"create_issue": False, "list_issues": True}

    def test_multiple_servers_isolated(self):
        tools = [
            {"type": "mcp_toolset", "mcp_server_name": "github"},
            {
                "type": "mcp_toolset",
                "mcp_server_name": "linear",
                "default_config": {"enabled": False},
            },
        ]
        rules = _build_mcp_toolset_rules(tools)
        assert set(rules) == {"github", "linear"}
        assert rules["linear"].default_enabled is False

    def test_configs_missing_name_ignored(self):
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "github",
            "configs": [{"enabled": False}],  # no 'name'
        }]
        rules = _build_mcp_toolset_rules(tools)
        assert rules["github"].per_tool == {}
# --- Claude MCP settings.json writer ---


class TestClaudeMcpToolsetRules:
    def test_empty_rules_no_settings_file(self):
        assert _tool_files_claude_mcp({}) == {}

    def test_bare_allow_no_settings_file(self):
        """Default-allow with no per-tool overrides produces no restrictions."""
        rules = {"gh": McpToolsetRules(default_enabled=True, per_tool={})}
        assert _tool_files_claude_mcp(rules) == {}

    def test_whole_server_deny_emits_server_in_deny(self):
        rules = {"gh": McpToolsetRules(default_enabled=False, per_tool={})}
        files = _tool_files_claude_mcp(rules)
        assert CLAUDE_SETTINGS_PATH in files
        settings = json.loads(files[CLAUDE_SETTINGS_PATH])
        assert settings == {"permissions": {"deny": [{"tool": "mcp__gh"}]}}

    def test_per_tool_deny_emits_full_tool_name(self):
        rules = {"gh": McpToolsetRules(
            default_enabled=True, per_tool={"create_issue": False},
        )}
        files = _tool_files_claude_mcp(rules)
        settings = json.loads(files[CLAUDE_SETTINGS_PATH])
        assert settings == {
            "permissions": {"deny": [{"tool": "mcp__gh__create_issue"}]},
        }

    def test_default_disabled_with_allow_emits_both(self):
        rules = {"gh": McpToolsetRules(
            default_enabled=False, per_tool={"list_issues": True},
        )}
        files = _tool_files_claude_mcp(rules)
        settings = json.loads(files[CLAUDE_SETTINGS_PATH])
        assert settings == {
            "permissions": {
                "allow": [{"tool": "mcp__gh__list_issues"}],
                "deny": [{"tool": "mcp__gh"}],
            },
        }

    def test_claude_wrapper_writes_settings_file_when_mcp_rules_active(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "gh",
            "default_config": {"enabled": False},
        }]
        script = build_wrapper_script(
            RUNTIMES["claude"], "k", "p", mcp_servers=servers, tools=tools,
        )
        assert CLAUDE_SETTINGS_PATH in script
        assert '"tool": "mcp__gh"' in script

    def test_claude_no_settings_file_when_no_rules(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        script = build_wrapper_script(
            RUNTIMES["claude"], "k", "p", mcp_servers=servers, tools=[],
        )
        assert CLAUDE_SETTINGS_PATH not in script


# --- mcp_toolset validation: new shape checks ---


@pytest.mark.django_db
class TestMcpToolsetValidation:
    def test_unknown_server_name_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{"type": "mcp_toolset", "mcp_server_name": "nope"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "nope" in str(resp.json()["detail"])

    def test_known_server_name_accepted(self, client: Client, auth_headers, runtime_key):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Good",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{"type": "mcp_toolset", "mcp_server_name": "gh"}],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201

    def test_configs_non_list_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{
                    "type": "mcp_toolset", "mcp_server_name": "gh",
                    "configs": "not-a-list",
                }],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "configs" in str(resp.json()["detail"])

    def test_configs_entry_name_non_string_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{
                    "type": "mcp_toolset", "mcp_server_name": "gh",
                    "configs": [{"name": 42}],
                }],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_configs_entry_enabled_non_bool_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{
                    "type": "mcp_toolset", "mcp_server_name": "gh",
                    "configs": [{"name": "x", "enabled": "yes"}],
                }],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_default_config_non_dict_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{
                    "type": "mcp_toolset", "mcp_server_name": "gh",
                    "default_config": "on",
                }],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_default_config_enabled_non_bool_rejected(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps({
                "name": "Bad",
                "model": "claude-sonnet-4-6",
                "runtime": "claude",
                "mcp_servers": [{"name": "gh", "url": "https://mcp.gh/mcp"}],
                "tools": [{
                    "type": "mcp_toolset", "mcp_server_name": "gh",
                    "default_config": {"enabled": "yes"},
                }],
            }),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422

    def test_update_rejects_toolset_for_removed_server(
        self, client: Client, auth_headers, user, runtime_key,
    ):
        agent = Agent.objects.create(
            user=user, name="Agent", model="claude-sonnet-4-6",
            runtime="claude",
            mcp_servers=[{"name": "gh", "url": "https://mcp.gh/mcp"}],
            tools=[{"type": "mcp_toolset", "mcp_server_name": "gh"}],
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent, version=1, name=agent.name,
            model=agent.model, runtime=agent.runtime,
            tools=agent.tools, mcp_servers=agent.mcp_servers,
        )
        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps({"version": 1, "mcp_servers": []}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "gh" in str(resp.json()["detail"])
# --- Codex MCP toolset rules emission ---


class TestCodexMcpToolsetRules:
    def _script(self, tools, servers):
        return build_wrapper_script(
            RUNTIMES["codex"], "k", "p", mcp_servers=servers, tools=tools,
        )

    def test_no_rules_no_tool_keys_in_server_block(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = []
        script = self._script(tools, servers)
        assert "[mcp_servers.gh]" in script
        assert "enabled_tools" not in script
        assert "disabled_tools" not in script
        assert "enabled = false" not in script

    def test_default_disabled_no_configs_emits_enabled_false(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "gh",
            "default_config": {"enabled": False},
        }]
        script = self._script(tools, servers)
        assert "enabled = false" in script

    def test_per_tool_deny_emits_disabled_tools(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "gh",
            "configs": [{"name": "create_issue", "enabled": False}],
        }]
        script = self._script(tools, servers)
        assert 'disabled_tools = ["create_issue"]' in script
        assert "enabled = false" not in script

    def test_default_disabled_with_allow_emits_enabled_tools(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "gh",
            "default_config": {"enabled": False},
            "configs": [{"name": "list_issues", "enabled": True}],
        }]
        script = self._script(tools, servers)
        assert 'enabled_tools = ["list_issues"]' in script

    def test_rules_for_other_server_do_not_leak(self):
        servers = [McpServerSpec(name="gh", type="url", url="https://mcp.gh/mcp")]
        tools = [{
            "type": "mcp_toolset",
            "mcp_server_name": "linear",  # validated away by view layer, ignored here
            "default_config": {"enabled": False},
        }]
        script = self._script(tools, servers)
        server_block = script.split("[mcp_servers.gh]")[1].split("MCP_EOF")[0]
        assert "enabled = false" not in server_block


# --- Gemini MCP toolset rules emission ---

