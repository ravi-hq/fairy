"""E2E tests verifying Agent.mcp_servers reaches the runtime.

Spawns the MCP reference server `@modelcontextprotocol/server-everything`
as a stdio subprocess inside the sprite. No external network MCP host is
required; npx pulls the package on first run. The `echo` tool is used as
a deterministic signal the agent actually invoked an MCP tool.

Requirements:
    FAIRY_API_TOKEN   valid API key

Run: `make test-e2e-mcp`.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.conftest import (
    RUNTIME_MODELS,
    FairyClient,
    _unique,
    stream_all_output,
)

pytestmark = [pytest.mark.slow, pytest.mark.mcp_matrix]

MCP_SERVER_NAME = "everything"
MCP_ECHO_TOOL = "echo"
MCP_ECHO_SIGNAL = "MCP_ECHO_OK"

RUNTIME_MCP_TOOL_NAMES = {
    "claude":       f"mcp__{MCP_SERVER_NAME}__{MCP_ECHO_TOOL}",
    "claude-oauth": f"mcp__{MCP_SERVER_NAME}__{MCP_ECHO_TOOL}",
}

PROMPT_INVOKE = (
    f"Call the `{MCP_ECHO_TOOL}` tool from the `{MCP_SERVER_NAME}` MCP server "
    f"with argument message={MCP_ECHO_SIGNAL!r}. Print the tool's exact response."
)
PROMPT_NO_MCP = (
    "List any MCP tools you have access to. If you have no MCP tools available, "
    "respond with NO_MCP_TOOLS."
)


def _parse_claude_mcp_tool_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for e in events:
        if e.get("type") != "output":
            continue
        try:
            obj = json.loads(e.get("data", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if obj.get("type") == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "")
                    if name.startswith("mcp__"):
                        names.append(name)
    return names


def _mcp_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    target = RUNTIME_MCP_TOOL_NAMES[runtime]
    if runtime in ("claude", "claude-oauth"):
        return target in _parse_claude_mcp_tool_names(events)
    return False


def _any_mcp_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    if runtime in ("claude", "claude-oauth"):
        return bool(_parse_claude_mcp_tool_names(events))
    return False


def _everything_server_spec() -> dict:
    return {
        "type": "stdio",
        "name": MCP_SERVER_NAME,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
    }


@pytest.fixture(scope="class", params=["claude"])
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


class TestMcpServerToolInvocable:
    """Server declared → agent invokes the MCP tool and we see its echo."""

    def test_mcp_server_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            mcp_servers=[_everything_server_spec()],
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPT_INVOKE, timeout=180,
        )
        final, events = api.run_session(session["id"])
        output = stream_all_output(events)
        assert final["status"] == "completed", (
            f"Session status={final['status']} exit={final.get('exit_code')}\n"
            f"Output: {output[:500]}"
        )
        assert _mcp_tool_was_invoked(events, runtime), (
            f"Expected MCP tool {RUNTIME_MCP_TOOL_NAMES[runtime]!r} to be invoked.\n"
            f"Output: {output[:500]}"
        )
        assert MCP_ECHO_SIGNAL in output, (
            f"Expected echo signal {MCP_ECHO_SIGNAL!r} in session output.\n"
            f"Output: {output[:500]}"
        )


class TestNoMcpServerNoMcpTool:
    """No mcp_servers → no mcp__* tool calls."""

    def test_no_mcp_server_no_mcp_tool(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-noserver-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            mcp_servers=[],
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPT_NO_MCP, timeout=90,
        )
        final, events = api.run_session(session["id"])
        assert final["status"] == "completed", (
            f"Session failed: {final['status']}\n"
            f"Output: {stream_all_output(events)[:500]}"
        )
        assert not _any_mcp_tool_was_invoked(events, runtime), (
            f"MCP tool invoked despite no mcp_servers on {runtime}.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )
