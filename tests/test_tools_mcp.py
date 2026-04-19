import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey, UserRuntimeKey, UserSpritesKey


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


# --- Session + MCP integration (via recording fake) ---


def _mcp_json(sprite) -> dict:
    return json.loads(sprite.write_map()["/tmp/mcp.json"])


@pytest.mark.django_db
class TestSessionMcpIntegration:
    def test_claude_mcp_url_server_writes_json_and_flags(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
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
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.last_sprite()
        cfg = _mcp_json(sprite)
        assert cfg == {
            "mcpServers": {"github": {"type": "http", "url": "https://mcp.github.com/mcp"}}
        }
        # The dispatcher script picks up --mcp-config flags
        run_script = sprite.write_map()["/run-agent.sh"]
        assert "--mcp-config /tmp/mcp.json" in run_script
        assert "--strict-mcp-config" in run_script

    def test_claude_mcp_with_headers(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
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
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cfg = _mcp_json(fake_sprites.last_sprite())
        assert cfg["mcpServers"]["private"]["headers"] == {"Authorization": "Bearer ${SECRET}"}

    def test_claude_stdio_mcp(self, client: Client, auth_headers, runtime_key, user, fake_sprites):
        agent = Agent.objects.create(
            user=user,
            name="Local MCP",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            mcp_servers=[
                {
                    "type": "stdio",
                    "name": "local",
                    "command": "npx",
                    "args": ["-y", "@some/mcp-server"],
                    "env": {"API_KEY": "val"},
                },
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cfg = _mcp_json(fake_sprites.last_sprite())
        assert cfg["mcpServers"]["local"]["type"] == "stdio"
        assert cfg["mcpServers"]["local"]["command"] == "npx"
        assert cfg["mcpServers"]["local"]["args"] == ["-y", "@some/mcp-server"]

    def test_codex_mcp_writes_toml(
        self, client: Client, auth_headers, user, sprites_key, fake_sprites
    ):
        urk = UserRuntimeKey(user=user, runtime="codex")
        urk.set_api_key("k")
        urk.save()
        agent = Agent.objects.create(
            user=user,
            name="Codex Agent",
            model="gpt-4.1",
            runtime="codex",
            version=1,
            mcp_servers=[
                {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"},
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.last_sprite()
        toml = sprite.write_map()["/home/sprite/.codex/config.toml"]
        assert "[mcp_servers.github]" in toml
        assert 'url = "https://mcp.github.com/mcp"' in toml
        # Codex does NOT get --mcp-config CLI flags
        run_script = sprite.write_map()["/run-agent.sh"]
        assert "--mcp-config" not in run_script

    def test_codex_mcp_bearer_token_env_var(
        self, client: Client, auth_headers, user, sprites_key, fake_sprites
    ):
        urk = UserRuntimeKey(user=user, runtime="codex")
        urk.set_api_key("k")
        urk.save()
        agent = Agent.objects.create(
            user=user,
            name="Codex Auth",
            model="gpt-4.1",
            runtime="codex",
            version=1,
            mcp_servers=[
                {
                    "type": "url",
                    "name": "api",
                    "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer ${MY_TOKEN}"},
                },
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        toml = fake_sprites.last_sprite().write_map()["/home/sprite/.codex/config.toml"]
        assert 'bearer_token_env_var = "MY_TOKEN"' in toml

    def test_gemini_mcp_writes_settings_json(
        self, client: Client, auth_headers, user, sprites_key, fake_sprites
    ):
        urk = UserRuntimeKey(user=user, runtime="gemini")
        urk.set_api_key("k")
        urk.save()
        agent = Agent.objects.create(
            user=user,
            name="Gemini Agent",
            model="gemini-2.5-pro",
            runtime="gemini",
            version=1,
            mcp_servers=[
                {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"},
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        settings = json.loads(
            fake_sprites.last_sprite().write_map()["/home/sprite/.gemini/settings.json"]
        )
        assert settings["mcpServers"]["github"]["httpUrl"] == "https://mcp.github.com/mcp"
        assert settings["mcpServers"]["github"]["trust"] is True

    def test_agent_without_mcp_writes_no_config(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Plain Agent",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        writes = fake_sprites.last_sprite().write_map()
        assert "/tmp/mcp.json" not in writes
        run_script = writes["/run-agent.sh"]
        assert "--mcp-config" not in run_script

    def test_multiple_mcp_servers(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        agent = Agent.objects.create(
            user=user,
            name="Multi MCP",
            model="claude-sonnet-4-6",
            runtime="claude",
            version=1,
            mcp_servers=[
                {"type": "url", "name": "github", "url": "https://mcp.github.com/mcp"},
                {"type": "url", "name": "slack", "url": "https://mcp.slack.com/mcp"},
            ],
        )
        resp = client.post(
            "/sessions",
            data=json.dumps({"agent_id": str(agent.id), "prompt": "hello"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        cfg = _mcp_json(fake_sprites.last_sprite())
        assert set(cfg["mcpServers"].keys()) == {"github", "slack"}

    def test_continue_session_only_writes_prompt_file(
        self, client: Client, auth_headers, runtime_key, user, fake_sprites
    ):
        """Follow-up /prompt is a minimal operation: it updates the prompt
        file on the Sprite and invokes the existing dispatcher in `continue`
        mode. Nothing else should be written."""
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
        resp = client.post(
            f"/sessions/{session.id}/prompt",
            data=json.dumps({"prompt": "follow-up"}),
            content_type="application/json",
            **auth_headers,
        )
        assert resp.status_code == 202
        sprite = fake_sprites.sprites["sprite-xyz"]
        assert len(sprite.writes) == 1
        assert sprite.writes[0].path == "/tmp/aod-prompt.txt"
        assert sprite.writes[0].text == "follow-up"


# --- Agent CRUD with mcp_servers (HTTP layer only, unchanged) ---


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
