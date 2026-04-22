from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


OPENCODE_VERSION = "1.14.20"

# Opencode (Bun-based) calls fs.access on $HOME/.opencode (legacy config probe)
# and walks $cwd up looking for .git (project root). On the Sprite base image
# /home/sprite is owned by ubuntu:ubuntu mode 750 — the sprite user can use
# the directory via a non-POSIX-ACL mechanism, but Bun's access check fails
# anyway with `PermissionDenied: FileSystem.access (...)`. Pin both HOME and
# cwd into a sprite-writable tmpfs dir so neither walk reaches /home/sprite.
OPENCODE_HOME = "/tmp/aod-opencode"
OPENCODE_CONFIG_DIR = f"{OPENCODE_HOME}/.config/opencode"


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

    HOME and cwd are pinned to `OPENCODE_HOME` for every turn — see the
    constant's comment for the underlying Bun/sprite-image issue.
    """

    name = "opencode"
    providers: set[str] = {"anthropic", "openai", "google"}
    skills_root: str | None = f"{OPENCODE_CONFIG_DIR}/skills"

    def install(self, sprite: Sprite) -> None:
        # 1. npm install -g puts the binary in nvm's prefix bin (not on PATH).
        # 2. Symlink it into ~/.local/bin so the per-turn `bash -lc` shim
        #    finds it (~/.local/bin is first on the sprite user's PATH).
        # 3. Pre-create the opencode HOME + config dir so write_config and
        #    the per-turn `cd` succeed.
        sprite.command(
            "bash",
            "-lc",
            (
                f"npm install -g opencode-ai@{OPENCODE_VERSION} && "
                f"mkdir -p /home/sprite/.local/bin {shlex.quote(OPENCODE_CONFIG_DIR)} && "
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
        # Wrap with `cd $OPENCODE_HOME && exec env HOME=$OPENCODE_HOME …` so
        # neither opencode's project-root walk nor its legacy-config probe
        # ever traverses /home/sprite. The outer per-turn shim (in
        # session_service/tasks.py) sources /tmp/aod-env then `exec`s this
        # bash, which in turn `exec`s opencode — net process count is the
        # same as for other runtimes.
        wrapped = (
            f"cd {shlex.quote(OPENCODE_HOME)} && "
            f"exec env HOME={shlex.quote(OPENCODE_HOME)} {shlex.join(argv)}"
        )
        return ["bash", "-c", wrapped]

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
        path = f"{OPENCODE_CONFIG_DIR}/opencode.json".lstrip("/")
        (fs / path).write_text(json.dumps({"mcp": config}, indent=2))
