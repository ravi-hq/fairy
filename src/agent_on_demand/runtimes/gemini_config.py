"""Build Gemini's MCP-server config dict from a list of `McpServerSpec`.

Extracted from `runtimes/gemini.py` so the mapping logic can be
mutation-tested in isolation. The original `GeminiRuntime.write_config`
keeps the JSON encode + sprite filesystem write; this module is pure.

Gemini's MCP config has two quirks worth pinning:

  - URL servers land under ``httpUrl`` (not ``url`` like Codex/Claude).
  - Every server entry — URL or stdio — carries ``trust: true``. This
    is the bit that authorizes the agent to invoke the server's tools
    without per-call confirmation; if a refactor drops it, every tool
    call would silently start prompting for confirmation and break the
    headless agent flow.

Like Claude (and unlike Codex), Gemini is permissive about unknown
header shapes — they're written verbatim — and unknown server types
are silently skipped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec


def render_gemini_mcp_config(mcp_servers: list[McpServerSpec]) -> dict[str, dict] | None:
    """Build the ``{server_name: entry}`` mapping that lands under the
    ``"mcpServers"`` key in Gemini's ``.gemini/settings.json``.

    Returns ``None`` when there's nothing to write — either an empty
    input list, or only unknown-type servers (which are silently
    skipped, matching the existing behavior). The empty-list case is
    handled by the same final ``if not config`` guard, since iterating
    an empty list never enters the loop body.
    """
    config: dict[str, dict] = {}
    for s in mcp_servers:
        if s.type == "url":
            entry: dict = {"httpUrl": s.url, "trust": True}
            if s.headers:
                entry["headers"] = s.headers
            config[s.name] = entry
        elif s.type == "stdio":
            entry = {"command": s.command, "args": s.args, "trust": True}
            if s.env:
                entry["env"] = s.env
            config[s.name] = entry
    if not config:
        return None
    return config
