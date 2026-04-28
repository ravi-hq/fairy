from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from agent_on_demand.runtimes.opencode_command import build_opencode_command
from agent_on_demand.runtimes.opencode_config import render_opencode_mcp_config

if TYPE_CHECKING:
    from agent_on_demand.session_service.backend import SessionHandle
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


OPENCODE_VERSION = "0.5.0"


class OpencodeRuntime:
    """Runtime for sst/opencode — a multi-provider meta-runtime CLI.

    One `opencode` binary fronts 75+ providers; the provider+model is picked
    per invocation via `--model provider/model_id`. Opencode reads the native
    provider env vars (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY)
    directly, which the env-file writer already dumps for every registered
    `UserCredential`, so no auth plumbing is needed here.

    Not pre-installed on the Sprite base image — `install` runs `npm i -g
    opencode-ai@<pinned>` on each provision. If the Environment has
    `networking_type="limited"`, the allowed-hosts list must include
    `registry.npmjs.org`.
    """

    name = "opencode"
    providers: set[str] = {"anthropic", "openai", "google"}
    skills_root: str | None = "/home/sprite/.config/opencode/skills"
    skills_sh_agent: str | None = "opencode"

    def install(self, handle: "SessionHandle") -> None:
        handle.make_command("bash", "-lc", f"npm install -g opencode-ai@{OPENCODE_VERSION}").run()

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        return build_opencode_command(spec, mode)

    def write_config(
        self,
        handle: "SessionHandle",
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config = render_opencode_mcp_config(mcp_servers)
        if config is None:
            return
        handle.workspace().write_text(
            "/home/sprite/.config/opencode/opencode.json",
            json.dumps({"mcp": config}, indent=2),
        )
