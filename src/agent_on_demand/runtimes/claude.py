from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from agent_on_demand.runtimes.claude_command import build_claude_command
from agent_on_demand.runtimes.claude_config import render_claude_mcp_config

if TYPE_CHECKING:
    from agent_on_demand.session_service.backends import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class ClaudeRuntime:
    """Runtime for Anthropic's Claude Code CLI.

    Auth comes from the env file written during provisioning
    (ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN); the CLI picks it up on
    its own, so the command shape is the same for both.
    """

    name = "claude"
    providers: set[str] = {"anthropic"}
    skills_root: str | None = "/home/sprite/.claude/skills"
    skills_sh_agent: str | None = "claude-code"

    def install(self, handle: "SessionHandle") -> None:
        return None

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
