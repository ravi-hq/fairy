from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sprites import Sprite

from agent_on_demand.runtimes.codex_config import render_codex_mcp_config

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class CodexRuntime:
    """Runtime for OpenAI's Codex CLI."""

    name = "codex"
    providers: set[str] = {"openai"}
    skills_root: str | None = "/home/sprite/.codex/skills"
    skills_sh_agent: str | None = "codex"

    def install(self, sprite: Sprite) -> None:
        return None

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        if mode == "continue":
            return [
                "codex",
                "exec",
                "resume",
                "--last",
                "--dangerously-bypass-approvals-and-sandbox",
                "--json",
            ]
        return [
            "codex",
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
        ]

    def write_config(
        self,
        sprite: Sprite,
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        # The TOML rendering — including Codex's strict bearer-token and
        # type validation — lives in agent_on_demand.runtimes.codex_config
        # so it can be mutation-tested without a Sprite. Skip the file
        # write entirely when there's nothing to render; matches the
        # historical behavior of writing the config file only on demand.
        if not mcp_servers:
            return
        body = render_codex_mcp_config(mcp_servers)
        fs = sprite.filesystem()
        (fs / "home/sprite/.codex/config.toml").write_text(body)
