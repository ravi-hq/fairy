from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


OPENCODE_VERSION = "1.14.20"


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

    def install(self, sprite: Sprite) -> None:
        # 1. npm install -g puts the binary in nvm's prefix bin (not on PATH).
        # 2. Symlink it into ~/.local/bin so the per-turn `bash -lc` shim
        #    finds it (~/.local/bin is first on the sprite user's PATH).
        # 3. Pre-create ~/.opencode so opencode's first-run legacy-config
        #    migration can read it instead of EACCES'ing.
        sprite.command(
            "bash",
            "-lc",
            (
                f"npm install -g opencode-ai@{OPENCODE_VERSION} && "
                "mkdir -p /home/sprite/.local/bin /home/sprite/.opencode && "
                'ln -sf "$(npm config get prefix)/bin/opencode" '
                "/home/sprite/.local/bin/opencode"
            ),
        ).run()

    def build_command(
        self, spec: "SessionSpec", mode: Literal["run", "continue"]
    ) -> list[str]:
        argv = ["opencode", "run", "--model", spec.model, "--format", "json"]
        if mode == "continue":
            argv.append("--continue")
        return argv

    def write_config(
        self,
        sprite: Sprite,
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        config: dict[str, dict] = {}
        for s in mcp_servers:
            if s.type == "url":
                entry: dict = {"type": "remote", "url": s.url, "enabled": True}
                if s.headers:
                    entry["headers"] = s.headers
            elif s.type == "stdio":
                entry = {
                    "type": "local",
                    "command": [s.command, *s.args],
                    "enabled": True,
                }
                if s.env:
                    entry["environment"] = s.env
            else:
                continue
            config[s.name] = entry
        if not config:
            return
        fs = sprite.filesystem()
        (fs / "home/sprite/.config/opencode/opencode.json").write_text(
            json.dumps({"mcp": config}, indent=2)
        )
