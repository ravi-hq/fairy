from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from sprites import Sprite

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
        if not mcp_servers:
            return
        lines: list[str] = []
        for s in mcp_servers:
            lines.append(f"[mcp_servers.{s.name}]")
            if s.type == "url":
                lines.append(f'url = "{s.url}"')
                for key, val in s.headers.items():
                    if key.lower() == "authorization" and val.startswith("Bearer "):
                        token = val.removeprefix("Bearer ").strip()
                        if token.startswith("${") and token.endswith("}"):
                            lines.append(f'bearer_token_env_var = "{token[2:-1]}"')
                lines.append("required = true")
            elif s.type == "stdio":
                lines.append(f'command = "{s.command}"')
                if s.args:
                    args_str = ", ".join(f'"{a}"' for a in s.args)
                    lines.append(f"args = [{args_str}]")
                if s.env:
                    lines.append(f"[mcp_servers.{s.name}.env]")
                    for key, val in s.env.items():
                        lines.append(f'{key} = "{val}"')
            lines.append("")
        fs = sprite.filesystem()
        (fs / "home/sprite/.codex/config.toml").write_text("\n".join(lines))
