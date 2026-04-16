"""E2E tests for agent CRUD, versioning, tools, and MCP servers."""

import uuid

import pytest

from tests.e2e.conftest import RUNTIME_MODELS, _unique


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestAgentCRUD:
    def test_create_agent_full(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-agent"),
            model="claude-sonnet-4-6",
            runtime="claude",
            system="You are a helpful assistant.",
            description="E2E test agent",
            skills=[{"type": "web_search"}],
            metadata={"team": "e2e"},
        )
        assert agent["type"] == "agent"
        assert agent["name"].startswith("e2e-agent-")
        assert agent["model"] == "claude-sonnet-4-6"
        assert agent["runtime"] == "claude"
        assert agent["system"] == "You are a helpful assistant."
        assert agent["description"] == "E2E test agent"
        assert agent["skills"] == [{"type": "web_search"}]
        assert agent["metadata"] == {"team": "e2e"}
        assert agent["version"] == 1
        assert agent["archived_at"] is None

    def test_create_agent_minimal(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-min"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        assert agent["system"] is None or agent["system"] == ""
        assert agent["skills"] == []
        assert agent["tools"] == []
        assert agent["mcp_servers"] == []
        assert agent["metadata"] == {}

    def test_create_agent_invalid_model_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-bad"),
            model="not-a-model",
            runtime="claude",
        )
        assert resp.status_code == 422

    def test_create_agent_invalid_runtime_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-bad"),
            model="claude-sonnet-4-6",
            runtime="invalid",
        )
        assert resp.status_code == 400

    def test_create_agent_missing_fields_rejected(self, api):
        resp = api.create_agent(name=_unique("e2e-bad"))
        assert resp.status_code == 422

    def test_get_agent(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-get"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.get_agent(agent["id"])
        assert resp.status_code == 200
        assert resp.json()["id"] == agent["id"]

    def test_get_agent_not_found(self, api):
        resp = api.get_agent(str(uuid.uuid4()))
        assert resp.status_code == 404

    def test_list_agents(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-list"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.list_agents()
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()["data"]]
        assert agent["id"] in ids


# ---------------------------------------------------------------------------
# Update & versioning
# ---------------------------------------------------------------------------


class TestAgentVersioning:
    def test_update_increments_version(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-ver"),
            model="claude-sonnet-4-6",
            runtime="claude",
            system="v1 prompt",
        )
        resp = api.update_agent(
            agent["id"],
            version=1,
            system="v2 prompt",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == 2
        assert data["system"] == "v2 prompt"
        assert data["name"] == agent["name"]  # unchanged

    def test_update_version_mismatch_rejected(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-vmis"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.update_agent(agent["id"], version=99, name="new")
        assert resp.status_code == 409

    def test_no_change_update_keeps_version(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-noop"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.update_agent(
            agent["id"],
            version=1,
            name=agent["name"],  # same value
        )
        assert resp.json()["version"] == 1

    def test_list_versions(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-versions"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        api.update_agent(agent["id"], version=1, system="updated")

        resp = api.list_agent_versions(agent["id"])
        assert resp.status_code == 200
        versions = resp.json()["data"]
        assert len(versions) == 2
        assert versions[0]["version"] == 2
        assert versions[1]["version"] == 1

    def test_metadata_merge_semantics(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-meta"),
            model="claude-sonnet-4-6",
            runtime="claude",
            metadata={"key1": "val1", "key2": "val2"},
        )
        # Merge: add key3, update key1, delete key2 (empty string)
        resp = api.update_agent(
            agent["id"],
            version=1,
            metadata={"key1": "updated", "key2": "", "key3": "new"},
        )
        data = resp.json()
        assert data["metadata"] == {"key1": "updated", "key3": "new"}


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------


class TestAgentArchive:
    def test_archive(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-arch"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.archive_agent(agent["id"])
        assert resp.status_code == 200
        assert resp.json()["archived_at"] is not None

    def test_archive_idempotent_409(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-arch2"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        api.archive_agent(agent["id"])
        resp = api.archive_agent(agent["id"])
        assert resp.status_code == 409

    def test_update_archived_rejected(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-archup"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        api.archive_agent(agent["id"])
        resp = api.update_agent(agent["id"], version=1, name="new")
        assert resp.status_code == 409

    def test_list_excludes_archived(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-archhide"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        api.archive_agent(agent["id"])
        resp = api.list_agents()
        ids = [a["id"] for a in resp.json()["data"]]
        assert agent["id"] not in ids

    def test_session_with_archived_agent_rejected(
        self, api, create_agent, e2e_runtimes
    ):
        if not e2e_runtimes:
            pytest.skip("No runtimes configured")
        runtime = e2e_runtimes[0]
        agent = create_agent(
            name=_unique("e2e-archsess"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
        )
        api.archive_agent(agent["id"])
        resp = api.create_session(agent_id=agent["id"], prompt="hello")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Tools & MCP servers
# ---------------------------------------------------------------------------


class TestAgentTools:
    def test_create_with_tools_and_mcp(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-tools"),
            model="claude-sonnet-4-6",
            runtime="claude",
            tools=[
                {"type": "agent_toolset_20260401"},
                {"type": "mcp_toolset", "mcp_server_name": "github"},
            ],
            mcp_servers=[
                {
                    "type": "url",
                    "name": "github",
                    "url": "https://mcp.github.com/mcp",
                },
            ],
        )
        assert len(agent["tools"]) == 2
        assert agent["tools"][0]["type"] == "agent_toolset_20260401"
        assert len(agent["mcp_servers"]) == 1
        assert agent["mcp_servers"][0]["name"] == "github"

    def test_create_with_custom_tool(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-custom"),
            model="claude-sonnet-4-6",
            runtime="claude",
            tools=[
                {
                    "type": "custom",
                    "name": "get_weather",
                    "description": "Get weather",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                },
            ],
        )
        assert agent["tools"][0]["name"] == "get_weather"

    def test_invalid_tool_type_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-badtool"),
            model="claude-sonnet-4-6",
            runtime="claude",
            tools=[{"type": "invalid"}],
        )
        assert resp.status_code == 422

    def test_custom_tool_missing_fields_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-badcustom"),
            model="claude-sonnet-4-6",
            runtime="claude",
            tools=[{"type": "custom", "name": "foo"}],
        )
        assert resp.status_code == 422

    def test_mcp_server_missing_name_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-badmcp"),
            model="claude-sonnet-4-6",
            runtime="claude",
            mcp_servers=[{"type": "url", "url": "https://example.com/mcp"}],
        )
        assert resp.status_code == 422

    def test_mcp_server_duplicate_names_rejected(self, api):
        resp = api.create_agent(
            name=_unique("e2e-dupmcp"),
            model="claude-sonnet-4-6",
            runtime="claude",
            mcp_servers=[
                {"name": "s", "url": "https://a.com/mcp"},
                {"name": "s", "url": "https://b.com/mcp"},
            ],
        )
        assert resp.status_code == 422

    def test_mcp_server_max_20_rejected(self, api):
        servers = [
            {"name": f"s{i}", "url": f"https://s{i}.com/mcp"}
            for i in range(21)
        ]
        resp = api.create_agent(
            name=_unique("e2e-maxmcp"),
            model="claude-sonnet-4-6",
            runtime="claude",
            mcp_servers=servers,
        )
        assert resp.status_code == 422

    def test_update_tools_versioned(self, api, create_agent):
        agent = create_agent(
            name=_unique("e2e-toolsver"),
            model="claude-sonnet-4-6",
            runtime="claude",
        )
        resp = api.update_agent(
            agent["id"],
            version=1,
            tools=[{"type": "agent_toolset_20260401"}],
            mcp_servers=[{"name": "s1", "url": "https://a.com/mcp"}],
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

        versions = api.list_agent_versions(agent["id"]).json()["data"]
        assert len(versions) == 2
        assert versions[0]["mcp_servers"] == [{"name": "s1", "url": "https://a.com/mcp"}]
        assert versions[1]["mcp_servers"] == []


# ---------------------------------------------------------------------------
# System prompt in sessions
# ---------------------------------------------------------------------------


class TestAgentSystemPrompt:
    """Verify the agent's system prompt is prepended to the user prompt."""

    @pytest.mark.slow
    def test_system_prompt_in_session(
        self, api, create_agent, create_session, e2e_runtimes
    ):
        if not e2e_runtimes:
            pytest.skip("No runtimes configured")
        runtime = e2e_runtimes[0]

        agent = create_agent(
            name=_unique("e2e-sysprompt"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            system=(
                "You must always include the word FAIRY_SYSTEM_MARKER "
                "in every response you give."
            ),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Say hello.",
            timeout=120,
        )
        from tests.e2e.conftest import stream_all_output

        result, events = api.run_session(session["id"])
        assert result["status"] == "completed"

        output = stream_all_output(events)
        assert "FAIRY_SYSTEM_MARKER" in output, (
            f"System prompt marker not found in output: {output[:500]}"
        )
