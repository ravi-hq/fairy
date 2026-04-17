"""E2E tests verifying Agent.mcp_servers + mcp_toolset are enforced by each runtime.

Per-rule translation is covered exhaustively by unit tests in tests/test_tools_mcp.py.
These tests only prove the translation layer reaches the runtime end-to-end, using the
Fairy-hosted /test-mcp server for deterministic tool signals.

Subsequent PRs in this stack widen the `runtime` fixture params from claude-only to
claude+codex+gemini.

Requirements:
    FAIRY_API_TOKEN   valid API key
    MCP_TEST_URL      URL of a reachable MCP test server. Defaults to
                      $FAIRY_API_URL/test-mcp, which is served by Fairy when
                      DEBUG=True or FAIRY_TESTING=1.

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

# No underscore in the server alias — avoids any parser ambiguity across runtimes.
MCP_TEST_SERVER_NAME = "testmcp"
MCP_TEST_TOOL_NAME = "signal_tool"
MCP_TEST_SIGNAL_PREFIX = "MCP_SIGNAL_"
MCP_TEST_SIGNAL_TOKEN = "OK"
MCP_DANGEROUS_SENTINEL = "SHOULD_NOT_BE_CALLED"

RUNTIME_MCP_TOOL_NAMES = {
    "claude":       f"mcp__{MCP_TEST_SERVER_NAME}__{MCP_TEST_TOOL_NAME}",
    "claude-oauth": f"mcp__{MCP_TEST_SERVER_NAME}__{MCP_TEST_TOOL_NAME}",
    "codex":        f"mcp__{MCP_TEST_SERVER_NAME}__{MCP_TEST_TOOL_NAME}",
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


# ---------------------------------------------------------------------------
# Parsers: extract mcp__server__tool names from each runtime's stream events
# ---------------------------------------------------------------------------

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


def _parse_codex_mcp_tool_names(events: list[dict]) -> list[str]:
    names: list[str] = []
    for e in events:
        if e.get("type") != "output":
            continue
        try:
            obj = json.loads(e.get("data", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if obj.get("type") in ("item.started", "item.completed"):
            item = obj.get("item", {})
            if item.get("type") == "mcp_tool_call":
                server = item.get("server", "")
                tool = item.get("tool", "")
                names.append(f"mcp__{server}__{tool}")
    return names


def _mcp_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    target = RUNTIME_MCP_TOOL_NAMES[runtime]
    if runtime in ("claude", "claude-oauth"):
        return target in _parse_claude_mcp_tool_names(events)
    if runtime == "codex":
        return target in _parse_codex_mcp_tool_names(events)
    return False


def _any_mcp_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    if runtime in ("claude", "claude-oauth"):
        return bool(_parse_claude_mcp_tool_names(events))
    if runtime == "codex":
        return bool(_parse_codex_mcp_tool_names(events))
    return False


# ---------------------------------------------------------------------------
# Tool-restriction builders
# ---------------------------------------------------------------------------

def _allow_all_agent_tools() -> dict:
    return {"type": "agent_toolset_20260401"}


def _mcp_server_spec(url: str) -> dict:
    return {"type": "url", "name": MCP_TEST_SERVER_NAME, "url": url}


def _mcp_toolset_allow_server() -> dict:
    return {"type": "mcp_toolset", "mcp_server_name": MCP_TEST_SERVER_NAME}


def _mcp_toolset_deny_server() -> dict:
    return {
        "type": "mcp_toolset",
        "mcp_server_name": MCP_TEST_SERVER_NAME,
        "default_config": {"enabled": False},
    }


def _mcp_toolset_deny_one_tool() -> dict:
    return {
        "type": "mcp_toolset",
        "mcp_server_name": MCP_TEST_SERVER_NAME,
        "configs": [{"name": MCP_TEST_TOOL_NAME, "enabled": False}],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def mcp_test_url(fairy_url):
    override = os.environ.get("MCP_TEST_URL")
    if override:
        return override
    return f"{fairy_url.rstrip('/')}/test-mcp"


@pytest.fixture(scope="class", params=["claude", "codex"])
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------

class TestMcpServerToolInvocable:
    """Server declared, no restrictions → agent invokes the MCP tool."""

    def test_mcp_server_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime, mcp_test_url,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=[_allow_all_agent_tools(), _mcp_toolset_allow_server()],
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


class TestMcpDenySpecificTool:
    """mcp_toolset.configs denies one tool → tool is not invoked."""

    def test_mcp_deny_specific_tool(
        self, api: FairyClient, create_agent, create_session, runtime, mcp_test_url,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-denytool-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=[_allow_all_agent_tools(), _mcp_toolset_deny_one_tool()],
            mcp_servers=[_mcp_server_spec(mcp_test_url)],
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPT_INVOKE, timeout=120,
        )
        _final, events = api.run_session(session["id"])
        output = stream_all_output(events)
        assert not _mcp_tool_was_invoked(events, runtime), (
            f"MCP tool {MCP_TEST_TOOL_NAME!r} was invoked despite deny on {runtime}.\n"
            f"Output: {output[:500]}"
        )


class TestMcpDenyEntireServer:
    """mcp_toolset default_config.enabled=False → no MCP tool from the server callable."""

    def test_mcp_deny_entire_server(
        self, api: FairyClient, create_agent, create_session, runtime, mcp_test_url,
    ):
        agent = create_agent(
            name=_unique(f"e2e-mcp-denyserver-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=[_allow_all_agent_tools(), _mcp_toolset_deny_server()],
            mcp_servers=[_mcp_server_spec(mcp_test_url)],
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPT_INVOKE, timeout=120,
        )
        _final, events = api.run_session(session["id"])
        output = stream_all_output(events)
        assert not _any_mcp_tool_was_invoked(events, runtime), (
            f"An MCP tool was invoked despite deny-entire-server on {runtime}.\n"
            f"Output: {output[:500]}"
        )
        assert MCP_DANGEROUS_SENTINEL not in output, (
            f"Dangerous sentinel {MCP_DANGEROUS_SENTINEL!r} in output — deny leaked.\n"
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
            tools=[_allow_all_agent_tools()],
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


class TestMcpDenyPersistsAcrossTurns:
    """MCP restrictions must re-apply on POST /sessions/{id}/prompt.

    Claude-only because multi-turn is exercised by other runtimes in test_sessions.py;
    here we only need to prove the deny rules file is re-emitted on continue.
    """

    def test_mcp_deny_persists(
        self, api: FairyClient, create_agent, create_session, e2e_runtimes, mcp_test_url,
    ):
        if "claude" not in e2e_runtimes:
            pytest.skip("claude not in E2E_RUNTIMES")
        agent = create_agent(
            name=_unique("e2e-mcp-multiturn"),
            model=RUNTIME_MODELS["claude"],
            runtime="claude",
            tools=[_allow_all_agent_tools(), _mcp_toolset_deny_server()],
            mcp_servers=[_mcp_server_spec(mcp_test_url)],
        )
        session = create_session(
            agent_id=agent["id"], prompt="Say 'hello'.", timeout=60,
        )
        api.run_session(session["id"])

        resp = api.send_prompt(session["id"], prompt=PROMPT_INVOKE, timeout=120)
        assert resp.status_code == 202

        _final, events = api.run_session(session["id"])
        assert not _any_mcp_tool_was_invoked(events, "claude"), (
            "MCP tool invoked on turn 2 — server deny did not persist.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )
