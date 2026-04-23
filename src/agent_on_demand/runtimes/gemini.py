from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

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
        config: dict[str, dict] = {}
        for s in mcp_servers:
            if s.type == "url":
                entry: dict = {"httpUrl": s.url, "trust": True}
                if s.headers:
                    entry["headers"] = s.headers
                config[s.name] = entry
            elif s.type == "stdio":
                entry = {"command": s.command, "args": s.args, "trust": True}
                if s.env:
                    entry["env"] = s.env
                config[s.name] = entry
        if not config:
            return
        fs = sprite.filesystem()
        (fs / "home/sprite/.gemini/settings.json").write_text(
            json.dumps({"mcpServers": config}, indent=2)
        )
