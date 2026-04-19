import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey, UserRuntimeKey, UserSpritesKey
from agent_on_demand.runtimes import RUNTIMES
from agent_on_demand.sprites_exec import McpServerSpec, build_wrapper_script


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
def sprites_key(user):
    usk = UserSpritesKey(user=user)
    usk.set_api_key("fake-sprites-token")
    usk.save()
    return usk


@pytest.fixture
def runtime_key(user, sprites_key):
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
    mocker.patch("agent_on_demand.views._get_client", return_value=mock_client)
    mocker.patch("agent_on_demand.views.threading.Thread")
    return mock_sprite, mock_fs


# --- Wrapper script MCP tests ---


class TestWrapperScriptMcp:
    def test_claude_mcp_config(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
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
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
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
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
        assert '"stdio"' in script
        assert '"npx"' in script
        assert "@some/mcp-server" in script

    def test_codex_mcp_config(self):
        config = RUNTIMES["codex"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
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
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
        assert 'bearer_token_env_var = "MY_TOKEN"' in script

    def test_gemini_mcp_config(self):
        config = RUNTIMES["gemini"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
        assert "~/.gemini/settings.json" in script
        assert '"httpUrl"' in script
        assert "https://mcp.github.com/mcp" in script
        assert '"trust": true' in script

    def test_no_mcp_backward_compat(self):
        config = RUNTIMES["claude"]
        script = build_wrapper_script(config, "sk-test")
        assert "--mcp-config" not in script
        assert "mcp.json" not in script

    def test_multiple_mcp_servers(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="github", type="url", url="https://mcp.github.com/mcp"),
            McpServerSpec(name="slack", type="url", url="https://mcp.slack.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
        assert '"github"' in script
        assert '"slack"' in script

    def test_mcp_section_before_exec(self):
        config = RUNTIMES["claude"]
        servers = [
            McpServerSpec(name="test", type="url", url="https://mcp.example.com/mcp"),
        ]
        script = build_wrapper_script(config, "sk-test", mcp_servers=servers)
        mcp_pos = script.index("MCP_EOF")
        exec_pos = script.index("exec ")
        assert mcp_pos < exec_pos


# --- Agent CRUD with mcp_servers ---


@pytest.mark.django_db
class TestCreateAgentWithMcp:
    def test_create_with_mcp_servers(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "MCP Agent",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [
                        {
                            "type": "url",
                            "name": "github",
                            "url": "https://mcp.github.com/mcp",
                        },
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "tools" not in data
        assert len(data["mcp_servers"]) == 1
        assert data["mcp_servers"][0]["name"] == "github"

    def test_create_without_mcp_defaults_empty(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "No MCP",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["mcp_servers"] == []
        assert "tools" not in data

    def test_tools_field_ignored_on_create(self, client: Client, auth_headers):
        """`tools` is no longer accepted — Pydantic silently drops unknown fields."""
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Ignored Tools",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "tools": [{"type": "agent_toolset_20260401"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 201
        assert "tools" not in resp.json()


@pytest.mark.django_db
class TestAgentMcpValidation:
    def test_mcp_server_missing_name(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [{"type": "url", "url": "https://example.com/mcp"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "name" in str(resp.json()["detail"]).lower()

    def test_mcp_server_url_type_missing_url(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [{"type": "url", "name": "test"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "url" in str(resp.json()["detail"]).lower()

    def test_mcp_server_stdio_type_missing_command(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [{"type": "stdio", "name": "test"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "command" in str(resp.json()["detail"]).lower()

    def test_mcp_server_duplicate_names(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [
                        {"name": "github", "url": "https://a.com/mcp"},
                        {"name": "github", "url": "https://b.com/mcp"},
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "duplicate" in str(resp.json()["detail"]).lower()

    def test_mcp_server_invalid_type(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": [{"type": "grpc", "name": "test", "url": "https://a.com"}],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "unknown type" in str(resp.json()["detail"]).lower()

    def test_mcp_server_max_20(self, client: Client, auth_headers):
        servers = [{"name": f"server-{i}", "url": f"https://s{i}.com/mcp"} for i in range(21)]
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "Bad",
                    "model": "claude-sonnet-4-6",
                    "runtime": "claude",
                    "mcp_servers": servers,
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 422
        assert "20" in str(resp.json()["detail"])


@pytest.mark.django_db
class TestUpdateAgentMcp:
    def test_update_mcp_servers(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent,
            version=1,
            name=agent.name,
            model=agent.model,
            runtime=agent.runtime,
        )

        resp = client.put(
            f"/agents/{agent.id}",
            data=json.dumps(
                {
                    "version": 1,
                    "mcp_servers": [
                        {"name": "github", "url": "https://mcp.github.com/mcp"},
                    ],
                }
            ),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert len(data["mcp_servers"]) == 1

    def test_update_mcp_versioned(self, client: Client, auth_headers, user):
        agent = Agent.objects.create(
            user=user,
            name="Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        AgentVersion.objects.create(
            agent=agent,
            version=1,
            name=agent.name,
            model=agent.model,
            runtime=agent.runtime,
        )

        client.put(
            f"/agents/{agent.id}",
            data=json.dumps(
                {
                    "version": 1,
                    "mcp_servers": [{"name": "s1", "url": "https://a.com/mcp"}],
                }
            ),
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
            user=user,
            name="MCP Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
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

        written_script = mock_fs.write_text.call_args_list[0][0][0]
        assert "--mcp-config" in written_script
        assert "github" in written_script
        assert "mcp.github.com" in written_script

    def test_session_agent_without_mcp_no_config(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Plain Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        _, mock_fs = mock_sprites
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        written_script = mock_fs.write_text.call_args_list[0][0][0]
        assert "--mcp-config" not in written_script

    def test_session_mcp_with_headers_in_config(
        self, client: Client, auth_headers, runtime_key, user, mock_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Auth Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
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

        written_script = mock_fs.write_text.call_args_list[0][0][0]
        assert "Authorization" in written_script
        assert "Bearer ${SECRET}" in written_script

    def test_continue_session_only_writes_prompt_file(
        self, client: Client, auth_headers, runtime_key, user, mocker, mock_sprites
    ):
        """Follow-up /prompt is a minimal operation: it updates the prompt
        file on the Sprite and invokes the existing wrapper script in
        `continue` mode. The script itself, baked at session-create time,
        already carries MCP config, env_vars, and skills — rebuilding would
        let mid-session agent edits leak into a running session.
        """
        from agent_on_demand.models import AgentSession

        agent = Agent.objects.create(
            user=user,
            name="MCP Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            mcp_servers=[
                {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"},
            ],
        )
        session = AgentSession.objects.create(
            user=user,
            agent=agent,
            runtime="claude",
            prompt="first",
            sprite_name="sprite-xyz",
            status="completed",
        )
        mock_sprite, mock_fs = mock_sprites
        mocker.patch(
            "agent_on_demand.views._get_client",
            return_value=mocker.MagicMock(get_sprite=mocker.Mock(return_value=mock_sprite)),
        )
        resp = client.post(
            f"/sessions/{session.id}/prompt",
            data=json.dumps({"prompt": "follow-up"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        # Only one filesystem write per prompt: the prompt file.
        assert mock_fs.write_text.call_count == 1
        written = mock_fs.write_text.call_args_list[0][0][0]
        assert written == "follow-up"

    def test_continue_session_config_snapshotted_at_create_time(
        self, client: Client, auth_headers, runtime_key, user, mocker, mock_sprites
    ):
        """Editing agent.mcp_servers after session creation must NOT affect an
        in-flight multi-turn session. The script is immutable post-create."""
        from agent_on_demand.models import AgentSession

        agent = Agent.objects.create(
            user=user,
            name="MCP Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            mcp_servers=[
                {"type": "url", "name": "original", "url": "https://mcp.original.com/mcp"},
            ],
        )
        session = AgentSession.objects.create(
            user=user,
            agent=agent,
            runtime="claude",
            prompt="first",
            sprite_name="sprite-xyz",
            status="completed",
        )
        # Mid-session agent update: should NOT propagate to the running session.
        agent.mcp_servers = [
            {"type": "url", "name": "changed", "url": "https://mcp.changed.com/mcp"},
        ]
        agent.version += 1
        agent.save()

        mock_sprite, mock_fs = mock_sprites
        mocker.patch(
            "agent_on_demand.views._get_client",
            return_value=mocker.MagicMock(get_sprite=mocker.Mock(return_value=mock_sprite)),
        )
        resp = client.post(
            f"/sessions/{session.id}/prompt",
            data=json.dumps({"prompt": "follow-up"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202

        # /prompt writes ONLY the prompt file — no new script is emitted with
        # the updated MCP config.
        assert mock_fs.write_text.call_count == 1
        written = mock_fs.write_text.call_args_list[0][0][0]
        assert written == "follow-up"
        assert "mcp.changed.com" not in written
        assert "mcp.original.com" not in written
