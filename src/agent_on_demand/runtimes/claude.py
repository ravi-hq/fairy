from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

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

    def install(self, sprite: Sprite) -> None:
        return None

    def build_command(
        self, spec: "SessionSpec", mode: Literal["run", "continue"]
    ) -> list[str]:
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
        config: dict[str, dict] = {}
        for s in mcp_servers:
            if s.type == "url":
                entry: dict = {"type": "http", "url": s.url}
                if s.headers:
                    entry["headers"] = s.headers
                config[s.name] = entry
            elif s.type == "stdio":
                entry = {"type": "stdio", "command": s.command, "args": s.args}
                if s.env:
                    entry["env"] = s.env
                config[s.name] = entry
        if not config:
            return
        fs = sprite.filesystem()
        (fs / "home/sprite/.claude.json").write_text(
            json.dumps({"mcpServers": config}, indent=2)
        )
