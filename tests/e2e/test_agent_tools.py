"""E2E tests verifying Agent.tools is enforced by each runtime CLI.

Tight matrix — per-tool translation is covered exhaustively by unit tests in
tests/test_tools_mcp.py. These tests only prove the translation layer works
end-to-end against a real Fairy deployment. One representative tool per runtime.

Run: `make test-e2e-tools`.
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

pytestmark = [pytest.mark.slow, pytest.mark.tool_matrix]

REPRESENTATIVE_TOOL = {
    "claude": "web_fetch",
    "claude-oauth": "web_fetch",
}

RUNTIME_TOOL_NAMES = {
    "claude": {"web_fetch": "WebFetch"},
    "claude-oauth": {"web_fetch": "WebFetch"},
}

PROMPTS = {
    "web_fetch": (
        "Use your web fetch tool to fetch https://httpbin.org/get and print the "
        "response body. Do not use shell."
    ),
}


def _parse_claude_tool_names(events: list[dict]) -> list[str]:
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
                    name = block.get("name")
                    if name:
                        names.append(name)
    return names


def _tool_was_invoked(events: list[dict], runtime: str, tool: str) -> bool:
    target = RUNTIME_TOOL_NAMES[runtime][tool]
    if runtime in ("claude", "claude-oauth"):
        return target in _parse_claude_tool_names(events)
    return False


def _any_tool_was_invoked(events: list[dict], runtime: str) -> bool:
    if runtime in ("claude", "claude-oauth"):
        return bool(_parse_claude_tool_names(events))
    return False


def _allow_only(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": False},
        "configs": [{"name": tool, "enabled": True}],
    }]


def _deny(tool: str) -> list[dict]:
    return [{
        "type": "agent_toolset_20260401",
        "default_config": {"enabled": True},
        "configs": [{"name": tool, "enabled": False}],
    }]


def _deny_all() -> list[dict]:
    return [{"type": "agent_toolset_20260401", "default_config": {"enabled": False}}]


@pytest.fixture(scope="class", params=["claude"])
def runtime(request, e2e_runtimes):
    if request.param not in e2e_runtimes:
        pytest.skip(f"{request.param} not in E2E_RUNTIMES")
    return request.param


class TestToolEnforcement:
    """Per-runtime matrix. Subsequent PRs widen the `runtime` fixture params."""

    def test_allow_tool_is_invocable(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        tool = REPRESENTATIVE_TOOL[runtime]
        agent = create_agent(
            name=_unique(f"e2e-allow-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_allow_only(tool),
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPTS[tool], timeout=120,
        )
        final, events = api.run_session(session["id"])
        assert final["status"] == "completed", (
            f"Session failed: status={final['status']}, "
            f"exit_code={final.get('exit_code')}\n"
            f"Output: {stream_all_output(events)[:500]}"
        )
        assert _tool_was_invoked(events, runtime, tool), (
            f"Expected '{tool}' ({RUNTIME_TOOL_NAMES[runtime][tool]}) to be "
            f"invoked.\nOutput: {stream_all_output(events)[:500]}"
        )

    def test_deny_tool_not_invoked(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        tool = REPRESENTATIVE_TOOL[runtime]
        agent = create_agent(
            name=_unique(f"e2e-deny-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_deny(tool),
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPTS[tool], timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, runtime, tool), (
            f"Tool '{tool}' was invoked despite being disabled.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )

    def test_deny_all_blocks_everything(
        self, api: FairyClient, create_agent, create_session, runtime,
    ):
        agent = create_agent(
            name=_unique(f"e2e-denyall-{runtime}"),
            model=RUNTIME_MODELS[runtime],
            runtime=runtime,
            tools=_deny_all(),
        )
        session = create_session(
            agent_id=agent["id"],
            prompt="Run `echo hello`, read /etc/hostname, and list /tmp files.",
            timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _any_tool_was_invoked(events, runtime), (
            f"Tool was invoked despite deny-all on {runtime}.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )


class TestClaudeOAuthSmoke:
    """Prove the claude-oauth path receives the same tool flags as claude."""

    def test_oauth_agent_respects_deny(
        self, api: FairyClient, create_agent, create_session, e2e_runtimes,
    ):
        if "claude-oauth" not in e2e_runtimes:
            pytest.skip("claude-oauth not in E2E_RUNTIMES")
        agent = create_agent(
            name=_unique("e2e-oauth-smoke"),
            model=RUNTIME_MODELS["claude-oauth"],
            runtime="claude-oauth",
            tools=_deny("web_fetch"),
        )
        session = create_session(
            agent_id=agent["id"], prompt=PROMPTS["web_fetch"], timeout=120,
        )
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, "claude-oauth", "web_fetch"), (
            f"web_fetch ran despite deny on claude-oauth.\n"
            f"Output: {stream_all_output(events)[:500]}"
        )


class TestMultiTurnPersistence:
    """Restrictions must re-apply on POST /sessions/{id}/prompt.

    Claude --continue does not persist CLI flags; Fairy rebuilds the wrapper
    script per call. This test proves the continue path re-emits flags.
    """

    def test_deny_persists_across_turns(
        self, api: FairyClient, create_agent, create_session, e2e_runtimes,
    ):
        if "claude" not in e2e_runtimes:
            pytest.skip("claude not in E2E_RUNTIMES")
        agent = create_agent(
            name=_unique("e2e-multi-turn"),
            model=RUNTIME_MODELS["claude"],
            runtime="claude",
            tools=_deny("web_fetch"),
        )
        session = create_session(
            agent_id=agent["id"], prompt="Say hello.", timeout=60,
        )
        api.run_session(session["id"])

        resp = api.send_prompt(
            session["id"], prompt=PROMPTS["web_fetch"], timeout=120,
        )
        assert resp.status_code == 202
        _final, events = api.run_session(session["id"])
        assert not _tool_was_invoked(events, "claude", "web_fetch"), (
            "web_fetch ran on turn 2 — restrictions did not persist."
        )
