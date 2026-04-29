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


# Minimum version that emits `claude_code.interaction` spans and honors
# inbound `TRACEPARENT` in `-p`/`--print` non-interactive mode (the
# Traces (beta) feature documented at
# https://code.claude.com/docs/en/monitoring-usage#traces-beta). The
# Sprite base image ships an older 2.1.92 that drops both, breaking the
# parent linkage we propagate from `session.execute_turn`.
CLAUDE_CODE_VERSION = "2.1.123"


class ClaudeRuntime:
    """Runtime for Anthropic's Claude Code CLI.

    Auth comes from the env file written during provisioning
    (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN); the CLI picks it up on
    its own, so the command shape is the same for both.

    The Sprite base image ships claude pre-installed but pinned to 2.1.92,
    which predates the Traces (beta) `TRACEPARENT` propagation. `install`
    upgrades it to `CLAUDE_CODE_VERSION` per provision so child spans
    parent under our `session.execute_turn` span. If the Environment has
    `networking_type="limited"`, the allowed-hosts list must include
    `registry.npmjs.org`.
    """

    name = "claude"
    providers: set[str] = {"anthropic"}
    skills_root: str | None = "/home/sprite/.claude/skills"
    skills_sh_agent: str | None = "claude-code"

    def install(self, handle: "SessionHandle") -> None:
        handle.make_command(
            "bash", "-lc", f"npm install -g @anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}"
        ).run()

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
