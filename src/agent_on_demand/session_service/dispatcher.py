"""Render the tiny /run-agent.sh dispatcher and compute per-runtime MCP CLI flags."""

from __future__ import annotations

from pathlib import Path

from agent_on_demand.runtimes import RuntimeConfig

ENV_FILE_PATH = "/tmp/aod-env"
RUN_SCRIPT_PATH = "/run-agent.sh"

_TEMPLATE_PATH = Path(__file__).parent / "run_agent.sh.tmpl"


def mcp_cmd_flags(runtime_name: str, has_mcp: bool) -> str:
    """Return extra CLI flags needed for MCP (only Claude needs explicit flags)."""
    if not has_mcp:
        return ""
    if runtime_name in ("claude", "claude-oauth"):
        return " --mcp-config /tmp/mcp.json --strict-mcp-config"
    return ""


def render_dispatcher_script(runtime: RuntimeConfig, *, has_mcp: bool) -> str:
    """Render the tiny per-turn dispatcher script.

    The dispatcher does no setup work — all provisioning happens inline during
    `provision_session`. It sources the env file, slurps the prompt from stdin,
    and execs the runtime CLI with the right continuation flags.
    """
    template = _TEMPLATE_PATH.read_text()
    flags = mcp_cmd_flags(runtime.name, has_mcp)
    replacements = {
        "@@RUN_CMD@@": f"{runtime.cmd}{flags}",
        "@@CONTINUE_CMD@@": f"{runtime.continue_cmd}{flags}",
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    return template
