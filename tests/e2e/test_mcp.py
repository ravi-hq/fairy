"""E2E tests verifying Agent.mcp_servers reaches the runtime.

Uses the Fairy-hosted /test-mcp server (served when DEBUG=True or
FAIRY_TESTING=1) for a deterministic `signal_tool` the agent can call.

Requirements:
    FAIRY_API_TOKEN   valid API key
    MCP_TEST_URL      URL of a reachable MCP test server. Defaults to
                      $FAIRY_API_URL/test-mcp.

Run: `make test-e2e-mcp`.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e.conftest import (
    RUNTIME_MODELS,
    FairyClient,
    _unique,
    stream_all_output,
)

pytestmark = [pytest.mark.slow, pytest.mark.mcp_matrix]

MCP_TEST_SERVER_NAME = "testmcp"
MCP_TEST_TOOL_NAME = "signal_tool"
MCP_TEST_SIGNAL_PREFIX = "MCP_SIGNAL_"
MCP_TEST_SIGNAL_TOKEN = "OK"

RUNTIME_MCP_TOOL_NAMES = {
    "claude":       f"mcp__{MCP_TEST_SERVER_NAME}__{MCP_TEST_TOOL_NAME}",
    "claude-oauth": f"mcp__{MCP_TEST_SERVER_NAME}__{MCP_TEST_TOOL_NAME}",
}

PROMPT_INVOKE = (
    f"Call the `{MCP_TEST_TOOL_NAME}` tool from the `{MCP_TEST_SERVER_NAME}` "
    f"MCP server with argument token={MCP_TEST_SIGNAL_TOKEN!r}. Print the tool's "
    f"exact response."
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


def _mcp_server_spec(url: str) -> dict:
    return {"type": "url", "name": MCP_TEST_SERVER_NAME, "url": url}


@pytest.fixture(scope="session")
def mcp_test_url(fairy_url):
    override = os.environ.get("MCP_TEST_URL")
    if override:
        return override
    return f"{fairy_url.rstrip('/')}/test-mcp"


@pytest.fixture(scope="class", params=["claude"])
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


class TestMcpServerToolInvocable:
    """Server declared → agent invokes the MCP tool."""

    def test_mcp_server_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime, mcp_test_url,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            mcp_servers=[_mcp_server_spec(mcp_test_url)],
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPT_INVOKE, timeout=120,
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
        assert MCP_TEST_SIGNAL_PREFIX in output, (
            f"Expected signal prefix {MCP_TEST_SIGNAL_PREFIX!r} in session output.\n"
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
