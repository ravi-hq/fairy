from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

from agent_on_demand.runtimes.claude_config import render_claude_mcp_config

if TYPE_CHECKING:
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

    def install(self, sprite: Sprite) -> None:
        return None

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        session_id = spec.runtime_session_id or ""
        return [
            "claude",
            "--dangerously-skip-permissions",
            "--print",
            "--verbose",
            "--output-format",
            "stream-json",
            "--resume" if mode == "continue" else "--session-id",
            session_id,
        ]

    def write_config(
        self,
        sprite: Sprite,
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config = render_claude_mcp_config(mcp_servers)
        if config is None:
            return
        fs = sprite.filesystem()
        (fs / "home/sprite/.claude.json").write_text(json.dumps({"mcpServers": config}, indent=2))
