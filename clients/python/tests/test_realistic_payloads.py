"""Round-trip tests against realistic server-shape payloads.

These payloads are hand-copied from the actual serializers in
`src/agent_on_demand/views/`. If the server's response shape drifts, these
fail loudly — unlike the minimal fixtures in conftest, which mostly guard
against regressions in the parsing glue.
"""

from __future__ import annotations

from aod import Agent, AgentVersion, Environment, EnvironmentVersion, Session


def test_agent_full_payload():
    raw = {
        "id": "c9d9ab0f-3e2f-4a08-9b58-39b65efb55b2",
        "type": "agent",
        "name": "demo",
        "description": "a test agent",
        "system": "you are helpful",
        "model": "claude-sonnet-4-5",
        "runtime": "claude-code",
        "environment_id": "a6aa9a1f-1abf-4f3e-8c8a-53fe3a8bd4c8",
        "skills": [{"name": "web", "config": {}}],
        "mcp_servers": [
            {
                "name": "everything",
                "type": "stdio",
                "command": "npx -y @modelcontextprotocol/server-everything",
            }
        ],
        "metadata": {"team": "platform"},
        "version": 3,
        "archived_at": None,
        "created_at": "2026-04-22T05:15:16.910301+00:00",
        "updated_at": "2026-04-22T05:15:16.910301+00:00",
    }
    agent = Agent.model_validate(raw)
    assert agent.type == "agent"
    assert agent.system == "you are helpful"
    assert agent.description == "a test agent"
    assert str(agent.environment_id) == "a6aa9a1f-1abf-4f3e-8c8a-53fe3a8bd4c8"
    assert agent.mcp_servers[0].type == "stdio"
    assert agent.mcp_servers[0].command.startswith("npx")


def test_agent_version_full_payload():
    raw = {
        "id": "c9d9ab0f-3e2f-4a08-9b58-39b65efb55b2",
        "type": "agent",
        "name": "demo",
        "description": None,
        "system": None,
        "model": "claude-sonnet-4-5",
        "runtime": "claude-code",
        "environment_id": None,
        "skills": [],
        "mcp_servers": [],
        "metadata": {},
        "version": 1,
        "created_at": "2026-04-22T05:15:16.910301+00:00",
    }
    AgentVersion.model_validate(raw)


def test_environment_full_payload():
    raw = {
        "id": "a6aa9a1f-1abf-4f3e-8c8a-53fe3a8bd4c8",
        "type": "environment",
        "name": "prod",
        "packages": {"apt": ["jq", "curl"], "npm": ["typescript"]},
        "setup_script": "echo hello",
        "networking": {"type": "limited", "allowed_hosts": ["api.github.com"]},
        "version": 2,
        "archived_at": None,
        "created_at": "2026-04-22T05:15:16.910301+00:00",
        "updated_at": "2026-04-22T05:15:16.910301+00:00",
    }
    env = Environment.model_validate(raw)
    assert env.packages == {"apt": ["jq", "curl"], "npm": ["typescript"]}
    assert env.networking.type == "limited"
    assert env.networking.allowed_hosts == ["api.github.com"]


def test_environment_version_full_payload():
    raw = {
        "id": "a6aa9a1f-1abf-4f3e-8c8a-53fe3a8bd4c8",
        "type": "environment",
        "name": "prod",
        "packages": {},
        "setup_script": None,
        "networking": {"type": "unrestricted"},
        "version": 1,
        "created_at": "2026-04-22T05:15:16.910301+00:00",
    }
    EnvironmentVersion.model_validate(raw)


def test_session_full_payload():
    raw = {
        "id": "3f5e8a8b-9d48-4d70-b7e2-5f1e2d3cabef",
        "agent_id": "c9d9ab0f-3e2f-4a08-9b58-39b65efb55b2",
        "environment_id": "a6aa9a1f-1abf-4f3e-8c8a-53fe3a8bd4c8",
        "runtime": "claude-code",
        "status": "completed",
        "exit_code": 0,
        "created_at": "2026-04-22T05:15:16.910301+00:00",
        "updated_at": "2026-04-22T05:20:00.000000+00:00",
        "resources": [
            {
                "type": "github_repository",
                "url": "https://github.com/ravi-hq/agent-on-demand",
                "mount_path": "/workspace/agent-on-demand",
            }
        ],
        "turn_count": 3,
        "current_turn": 3,
    }
    session = Session.model_validate(raw)
    assert session.runtime == "claude-code"
    assert session.exit_code == 0
    assert session.resources[0].type == "github_repository"
    assert session.resources[0].mount_path == "/workspace/agent-on-demand"
    assert session.turn_count == 3
