from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from agent_on_demand.session_service.backends import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


class Runtime(Protocol):
    name: str
    providers: set[str]  # which providers this runtime can serve; non-empty
    skills_root: str | None  # absolute path on Sprite for SKILL.md files, or None to disable
    skills_sh_agent: str | None  # `--agent` id for `npx skills add`, or None if unsupported

    def install(self, handle: SessionHandle) -> None:
        """Install the runtime CLI on the session. No-op if pre-installed in the base image."""

    def build_command(self, spec: SessionSpec, mode: Literal["run", "continue"]) -> list[str]:
        """Argv for the per-turn command. Prompt arrives via stdin."""

    def write_config(
        self, handle: SessionHandle, spec: SessionSpec, mcp_servers: list[McpServerSpec]
    ) -> None:
        """Write any per-runtime config files on the session. Always called at provision time,
        even when mcp_servers is empty."""
