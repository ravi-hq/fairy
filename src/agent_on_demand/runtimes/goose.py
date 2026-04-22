from __future__ import annotations

import yaml
from typing import TYPE_CHECKING, Literal

from sprites import Sprite

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec, SessionSpec


GOOSE_VERSION = "v1.31.1"  # pin to a specific tag for reproducibility


class GooseRuntime:
    """Runtime for block/goose — a multi-provider meta-runtime CLI.

    One `goose` binary fronts 15+ providers; the provider+model is picked
    per invocation via `--provider` + `--model` flags. Goose reads native
    provider env vars (ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY)
    from the env file sourced before exec.

    Not pre-installed on the Sprite base image — `install` runs apt-get +
    curl to download the goose release binary. GOOSE_DISABLE_KEYRING is
    required since containers have no system keyring.
    """

    name = "goose"
    providers: set[str] = {"anthropic", "openai", "google"}
    skills_root: str | None = None  # recipes are YAML; SKILL.md not supported in v1

    def install(self, sprite: Sprite) -> None:
        sprite.command(
            "bash",
            "-lc",
            f"apt-get install -y -qq bzip2 && curl -fsSL "
            f"https://github.com/block/goose/releases/download/{GOOSE_VERSION}/download_cli.sh "
            f"| CONFIGURE=false bash",
        ).run()

    def build_command(self, spec: "SessionSpec", mode: Literal["run", "continue"]) -> list[str]:
        provider, model_id = spec.model.split("/", 1)
        argv = [
            "goose",
            "run",
            "--instructions",
            "-",  # read prompt from stdin
            "--output-format",
            "stream-json",
            "--mode",
            "auto",  # bypass approval prompts (fully autonomous)
            "--name",
            spec.runtime_session_id or "",
            "--provider",
            provider,
            "--model",
            model_id,
        ]
        if mode == "continue":
            argv.append("--resume")
        return argv

    def write_config(
        self,
        sprite: Sprite,
        spec: "SessionSpec",
        mcp_servers: list["McpServerSpec"],
    ) -> None:
        provider, model_id = spec.model.split("/", 1)
        sprite.command("mkdir", "-p", "/home/sprite/.config/goose").run()
        config: dict = {
            "GOOSE_PROVIDER": provider,
            "GOOSE_MODEL": model_id,
            "GOOSE_MODE": "auto",
            "GOOSE_TELEMETRY_ENABLED": False,
            "GOOSE_DISABLE_KEYRING": True,  # no system keyring in container
            "extensions": {
                "developer": {
                    "type": "builtin",
                    "name": "developer",
                    "bundled": True,
                    "enabled": True,
                    "timeout": 300,
                },
            },
        }
        for s in mcp_servers:
            if s.type == "url":
                entry: dict = {
                    "type": "streamable_http",
                    "name": s.name,
                    "uri": s.url,
                    "enabled": True,
                }
                if s.headers:
                    entry["headers"] = s.headers
            elif s.type == "stdio":
                entry = {
                    "type": "stdio",
                    "name": s.name,
                    "cmd": s.command,
                    "args": s.args,
                    "envs": s.env,
                    "enabled": True,
                    "timeout": 300,
                }
            else:
                continue
            config["extensions"][s.name] = entry
        fs = sprite.filesystem()
        (fs / "home/sprite/.config/goose/config.yaml").write_text(yaml.safe_dump(config))
