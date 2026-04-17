"""E2E tests verifying Agent.mcp_servers reaches each runtime.

Spawns `@modelcontextprotocol/server-everything` as a stdio MCP
subprocess inside the sprite (pulled via `npx -y` on first run). No
external MCP host is required.

The agent is instructed to call the `echo` tool with a unique token
per test; the token appearing in the session output proves (a) the
runtime wired the MCP server in, (b) the agent could see the tool,
and (c) the tool actually ran. Parsing is runtime-agnostic — same
JSON-concat trick used by test_skills.py — so the same test body
covers claude, codex, gemini, and claude-oauth.

Requirements:
    FAIRY_API_TOKEN   valid API key

Run: `make test-e2e-mcp`.
"""

from __future__ import annotations

import json
import uuid

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


# Fields whose string values are event metadata, not model text. Same filter
# set as test_skills.py — keeps metadata from wedging between chunked content
# deltas and breaking substring matches.
_METADATA_KEYS = frozenset({
    "type", "role", "session_id", "model", "timestamp", "id", "tool_id",
    "tool_name", "tool_call_id", "tool_use_id", "parent_tool_use_id",
    "status", "finish_reason", "stop_reason", "event_type", "msg_type",
    "name", "uuid",
})


def _concat_json_strings(stream_output: str) -> str:
    """Glue chunked deltas back together across runtime output formats.

    All runtimes emit one JSON object per line with model text split across
    many ``content`` / ``text`` delta events. A raw substring match fails
    when the target string straddles two events. Walking every parsed event
    and concatenating string values (minus known metadata fields)
    reassembles the text regardless of shape.
    """
    parts: list[str] = []

    def walk(v):
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                if k in _METADATA_KEYS:
                    continue
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    for line in stream_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            parts.append(line)
    return "".join(parts)


def _everything_server_spec() -> dict:
    return {
        "type": "stdio",
        "name": MCP_SERVER_NAME,
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-everything"],
    }


@pytest.fixture(scope="class", params=list(RUNTIME_MODELS.keys()))
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


class TestMcpServerToolInvocable:
    """Server declared → agent calls echo and we see the unique signal."""

    def test_mcp_server_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        signal = f"MCP-ECHO-{uuid.uuid4().hex[:12]}"
        prompt = (
            f"Call the `{MCP_ECHO_TOOL}` tool from the `{MCP_SERVER_NAME}` "
            f"MCP server with argument message={signal!r}. Include the tool's "
            f"exact response in your reply."
        )
        agent = create_agent(
            name=_unique(f"e2e-mcp-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            mcp_servers=[_everything_server_spec()],
        )
        session = create_session(agent_id=agent["id"], prompt=prompt, timeout=240)
        final, events = api.run_session(session["id"])
        raw = stream_all_output(events)
        reassembled = _concat_json_strings(raw)

        assert final["status"] == "completed", (
            f"Session status={final['status']} exit={final.get('exit_code')}\n"
            f"Output: {raw[:500]}"
        )
        assert signal in reassembled, (
            f"Expected echo signal {signal!r} in reassembled output.\n"
            f"Output: {raw[:500]}"
        )


class TestNoMcpServerNoMcpTool:
    """No mcp_servers → agent self-reports having no MCP tools."""

    def test_no_mcp_server_no_mcp_tool(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        sentinel = "NO_MCP_TOOLS"
        prompt = (
            "List any MCP tools you have access to. If you have no MCP tools "
            f"available, respond with the exact text {sentinel!r}."
        )
        agent = create_agent(
            name=_unique(f"e2e-mcp-noserver-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            mcp_servers=[],
        )
        session = create_session(agent_id=agent["id"], prompt=prompt, timeout=90)
        final, events = api.run_session(session["id"])
        raw = stream_all_output(events)
        reassembled = _concat_json_strings(raw)

        assert final["status"] == "completed", (
            f"Session failed: {final['status']}\n"
            f"Output: {raw[:500]}"
        )
        assert sentinel in reassembled, (
            f"Expected sentinel {sentinel!r} in reassembled output — agent may "
            f"have invoked an MCP tool despite empty mcp_servers.\n"
            f"Output: {raw[:500]}"
        )
