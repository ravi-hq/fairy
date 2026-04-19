"""Render the tiny /run-agent.sh dispatcher."""

from __future__ import annotations

from pathlib import Path

from agent_on_demand.runtimes import RuntimeConfig

ENV_FILE_PATH = "/tmp/aod-env"
RUN_SCRIPT_PATH = "/run-agent.sh"

_TEMPLATE_PATH = Path(__file__).parent / "run_agent.sh.tmpl"


def render_dispatcher_script(runtime: RuntimeConfig) -> str:
    """Render the tiny per-turn dispatcher script.

    The dispatcher does no setup work — all provisioning happens inline during
    `provision_session`. It sources the env file, slurps the prompt from stdin,
    and execs the runtime CLI with the right continuation flags.
    """
    template = _TEMPLATE_PATH.read_text()
    replacements = {
        "@@RUN_CMD@@": runtime.cmd,
        "@@CONTINUE_CMD@@": runtime.continue_cmd,
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template
