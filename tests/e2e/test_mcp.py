"""E2E tests verifying Agent.mcp_servers reaches each runtime.

Spawns `@modelcontextprotocol/server-everything` as a stdio MCP
subprocess inside the sprite. The server is pre-installed via the
Environment's `packages.npm` so the stdio handshake is effectively
instant at session time — using `npx -y` inline raced with the
agent's first decision tick (Claude saw `status:"pending"` and
ToolSearched its way to "not available" before the server finished
connecting).

The agent is instructed to call the `echo` tool with a unique token
per test; the token appearing in the session output proves (a) the
runtime wired the MCP server in, (b) the agent could see the tool,
and (c) the tool actually ran. Parsing is runtime-agnostic — same
JSON-concat trick used by test_skills.py — so the same test body
covers claude, codex, and gemini.

Requirements:
    AOD_API_TOKEN   valid API key

Run: `make test-e2e-mcp`.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.conftest import (
    RUNTIME_MODELS,
    APIClient,
    _unique,
    stream_all_output,
)

pytestmark = [pytest.mark.slow, pytest.mark.mcp_matrix]

MCP_SERVER_NAME = "everything"
MCP_ECHO_TOOL = "echo"
MCP_SERVER_NPM_PKG = "@modelcontextprotocol/server-everything"


# Fields whose string values are event metadata, not model text. Same filter
# set as test_skills.py — keeps metadata from wedging between chunked content
# deltas and breaking substring matches.
_METADATA_KEYS = frozenset(
    {
        "type",
        "role",
        "session_id",
        "model",
        "timestamp",
        "id",
        "tool_id",
        "tool_name",
        "tool_call_id",
        "tool_use_id",
        "parent_tool_use_id",
        "status",
        "finish_reason",
        "stop_reason",
        "event_type",
        "msg_type",
        "name",
        "uuid",
    }
)


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
    # `npx` ships alongside node so it's reliably on PATH in every runtime's
    # stdio subprocess env (the bare `mcp-server-everything` binary isn't —
    # Claude Code's `-p` mode marks it status:"failed" at init). Because
    # packages.npm pre-installs the package, npx finds it in global
    # node_modules and invokes it without a download, killing the startup
    # race that plagued `npx -y` without the pre-install.
    return {
        "type": "stdio",
        "name": MCP_SERVER_NAME,
        "command": "npx",
        "args": ["--yes", MCP_SERVER_NPM_PKG],
    }


# claude-oauth is intentionally excluded — Anthropic OAuth accounts can have
# MCP disabled at the org policy tier (observed as "org_level_disabled" in the
# stream), so MCP invocation is not portably testable on that runtime. Other
# e2e suites still exercise claude-oauth for non-MCP paths.
_MCP_RUNTIMES = [r for r in RUNTIME_MODELS.keys() if r != "claude-oauth"]


@pytest.fixture(scope="class", params=_MCP_RUNTIMES)
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


class TestMcpServerToolInvocable:
    """Server declared → agent calls echo and we see the unique signal."""

    def test_mcp_server_tool_is_invocable(
        self,
        api: APIClient,
        create_agent,
        create_session,
        create_environment,
        runtime,
    ):
        signal = f"MCP-ECHO-{uuid.uuid4().hex[:12]}"
        prompt = (
            f"Call the `{MCP_ECHO_TOOL}` tool from the `{MCP_SERVER_NAME}` "
            f"MCP server with argument message={signal!r}. Include the tool's "
            f"exact response in your reply."
        )
        env = create_environment(
            name=_unique(f"e2e-mcp-env-{runtime}"),
            packages={"npm": [MCP_SERVER_NPM_PKG]},
        )
        agent = create_agent(
            name=_unique(f"e2e-mcp-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
            mcp_servers=[_everything_server_spec()],
        )
        session = create_session(agent_id=agent["id"], prompt=prompt, timeout=300)
        final, events = api.run_session(session["id"], timeout=300)
        raw = stream_all_output(events)
        reassembled = _concat_json_strings(raw)

        assert final["status"] == "completed", (
            f"Session status={final['status']} exit={final.get('exit_code')}\nOutput: {raw[:500]}"
        )
        assert signal in reassembled, (
            f"Expected echo signal {signal!r} in reassembled output.\nOutput: {raw[:500]}"
        )


class TestMcpPersistsAcrossTurns:
    """Regression: MCP config must be re-emitted on every /prompt turn.

    Prior to the fix, ``send_prompt`` rebuilt ``run-agent.sh`` without passing
    ``mcp_servers``. The resulting script never wrote the per-runtime MCP
    config file (e.g. ``~/.claude.json``), so MCP tools disappeared on every
    turn after the first. This test calls the MCP echo tool twice in the same
    session — once on create, once via ``send_prompt`` — with different
    signals, and asserts both fire.
    """

    def test_mcp_tool_invocable_on_second_turn(
        self,
        api: APIClient,
        create_agent,
        create_session,
        create_environment,
        runtime,
    ):
        signal1 = f"MCP-T1-{uuid.uuid4().hex[:12]}"
        signal2 = f"MCP-T2-{uuid.uuid4().hex[:12]}"
        env = create_environment(
            name=_unique(f"e2e-mcp-persist-env-{runtime}"),
            packages={"npm": [MCP_SERVER_NPM_PKG]},
        )
        agent = create_agent(
            name=_unique(f"e2e-mcp-persist-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            environment_id=env["id"],
            mcp_servers=[_everything_server_spec()],
        )

        turn1 = (
            f"Call the `{MCP_ECHO_TOOL}` tool from the `{MCP_SERVER_NAME}` "
            f"MCP server with argument message={signal1!r}. Include the tool's "
            f"exact response in your reply."
        )
        session = create_session(agent_id=agent["id"], prompt=turn1, timeout=300)
        final1, events1 = api.run_session(session["id"], timeout=300)
        raw1 = stream_all_output(events1)
        reassembled1 = _concat_json_strings(raw1)
        assert final1["status"] == "completed", (
            f"Turn 1 status={final1['status']} exit={final1.get('exit_code')}\nOutput: {raw1[:500]}"
        )
        assert signal1 in reassembled1, (
            f"Turn 1 echo signal {signal1!r} missing.\nOutput: {raw1[:500]}"
        )

        turn2 = (
            f"Call the `{MCP_ECHO_TOOL}` tool from the `{MCP_SERVER_NAME}` "
            f"MCP server again, this time with argument message={signal2!r}. "
            f"Include the tool's exact response in your reply."
        )
        resp = api.send_prompt(session["id"], prompt=turn2, timeout=300)
        assert resp.status_code == 202, f"send_prompt rejected: {resp.status_code} {resp.text}"

        final2, events2 = api.run_session(session["id"], timeout=300)
        raw2 = stream_all_output(events2)
        reassembled2 = _concat_json_strings(raw2)
        assert final2["status"] == "completed", (
            f"Turn 2 status={final2['status']} exit={final2.get('exit_code')}\nOutput: {raw2[:500]}"
        )
        assert signal2 in reassembled2, (
            f"Turn 2 echo signal {signal2!r} missing — MCP config likely not "
            f"rematerialized on continuation.\nOutput: {raw2[:500]}"
        )
