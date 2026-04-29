from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from agent_on_demand.runtimes.gemini_command import build_gemini_command
from agent_on_demand.runtimes.gemini_config import render_gemini_mcp_config

if TYPE_CHECKING:
    from agent_on_demand.session_service.backends import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class GeminiRuntime:
    """Runtime for Google's Gemini CLI."""

    name = "gemini"
    providers: set[str] = {"google"}
    skills_root: str | None = "/home/sprite/.gemini/skills"
    skills_sh_agent: str | None = "gemini-cli"

    def install(self, handle: "SessionHandle") -> None:
        return None

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        return build_gemini_command(spec, mode)

    def write_config(
        self,
        handle: "SessionHandle",
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config = render_gemini_mcp_config(mcp_servers)
        if config is None:
            return
        handle.workspace().write_text(
            "/home/sprite/.gemini/settings.json",
            json.dumps({"mcpServers": config}, indent=2),
        )

    def otel_env(
        self,
        spec: "SessionSpec",
        traceparent: str | None,
        tracestate: str | None,
    ) -> dict[str, str]:
        return {}
