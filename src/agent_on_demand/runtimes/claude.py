from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from agent_on_demand.runtimes.claude_command import build_claude_command
from agent_on_demand.runtimes.claude_config import render_claude_mcp_config
from agent_on_demand.runtimes.claude_otel import (
    build_claude_otel_env,
    build_claude_otel_static_env,
)

if TYPE_CHECKING:
    from agent_on_demand.session_service.backends import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class ClaudeRuntime:
    """Runtime for Anthropic's Claude Code CLI.

    Auth comes from the env file written during provisioning
    (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN); the CLI picks it up on
    its own, so the command shape is the same for both.

    The Sprite base image ships claude pre-installed but pinned to 2.1.92,
    which predates the Traces (beta) `TRACEPARENT` propagation documented
    at https://code.claude.com/docs/en/monitoring-usage#traces-beta. On
    that version, child spans land in Honeycomb as orphan roots instead
    of parenting under our `session.execute_turn` span. `install` runs
    `claude update` per provision to pull the latest CLI before the
    network policy locks down. If the Environment has
    `networking_type="limited"`, the allowed-hosts list must reach
    whatever update channel claude uses (npm registry today).
    """

    name = "claude"
    providers: set[str] = {"anthropic"}
    skills_root: str | None = "/home/sprite/.claude/skills"
    skills_sh_agent: str | None = "claude-code"

    def install(self, handle: "SessionHandle") -> None:
        handle.make_command("bash", "-lc", "claude update").run()

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        return build_claude_command(spec, mode)

    def write_config(
        self,
        handle: "SessionHandle",
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config = render_claude_mcp_config(mcp_servers)
        if config is None:
            return
        handle.workspace().write_text(
            "/home/sprite/.claude.json",
            json.dumps({"mcpServers": config}, indent=2),
        )

    def otel_env(
        self,
        spec: "SessionSpec",
        traceparent: str | None,
        tracestate: str | None,
    ) -> dict[str, str]:
        return build_claude_otel_env(spec, traceparent, tracestate)

    def static_env(self, spec: "SessionSpec") -> list[tuple[str, str]]:
        return build_claude_otel_static_env()
