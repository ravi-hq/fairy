from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

from agent_on_demand.runtimes.gemini_config import render_gemini_mcp_config

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class GeminiRuntime:
    """Runtime for Google's Gemini CLI."""

    name = "gemini"
    providers: set[str] = {"google"}
    skills_root: str | None = "/home/sprite/.gemini/skills"
    skills_sh_agent: str | None = "gemini-cli"

    def install(self, sprite: Sprite) -> None:
        return None

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        if mode == "continue":
            return ["gemini", "--resume", "--output-format", "stream-json"]
        return ["gemini", "--output-format", "stream-json"]

    def write_config(
        self,
        sprite: Sprite,
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config = render_gemini_mcp_config(mcp_servers)
        if config is None:
            return
        fs = sprite.filesystem()
        (fs / "home/sprite/.gemini/settings.json").write_text(
            json.dumps({"mcpServers": config}, indent=2)
        )
