"""HTTP-layer validation for the agent `mcp_servers` field.

Per-runtime MCP config rendering (the actual file contents on the Sprite) is
covered in `tests/runtimes/test_{claude,codex,gemini}.py`.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client

from agent_on_demand.models import Agent, AgentVersion, APIKey


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


# --- Agent CRUD with mcp_servers (HTTP layer) ---


@pytest.mark.django_db
class TestCreateAgentWithMcp:
    def test_create_with_mcp_servers(self, client: Client, auth_headers):
        resp = client.post(
            "/agents",
            data=json.dumps(
                {
                    "name": "MCP Agent",
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
                    "model": "anthropic/claude-sonnet-4-6",
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
            model="anthropic/claude-sonnet-4-6",
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
            model="anthropic/claude-sonnet-4-6",
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
