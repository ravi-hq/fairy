"""Build Opencode's MCP-server config dict from a list of `McpServerSpec`.

Extracted from `runtimes/opencode.py` so the mapping logic can be
mutation-tested in isolation. The original `OpencodeRuntime.write_config`
keeps the JSON encode + sprite filesystem write; this module is pure.

Opencode's MCP config has the most quirks of any runtime — five distinct
naming differences from Codex/Claude/Gemini, every one of which would
silently break MCP if a future refactor "normalizes" it:

  - URL servers land under ``type: "remote"`` (NOT ``"http"`` /
    ``"url"`` / ``"stdio"``).
  - stdio servers land under ``type: "local"`` (NOT ``"stdio"``).
  - For stdio, ``command`` and ``args`` are *merged into a single
    array* under the ``command`` key (``[cmd, *args]``) — opencode
    doesn't use a separate ``args`` field.
  - The env table key is ``environment`` (NOT ``env``).
  - Every entry carries ``enabled: true`` — drop this and the server
    is registered but won't actually be invoked.
  - The top-level config wrapper key is ``mcp`` (NOT ``mcpServers``)
    — handled by the caller via the JSON dump, but still part of the
    integration contract.

Unknown server types are silently skipped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec


def render_opencode_mcp_config(mcp_servers: list[McpServerSpec]) -> dict[str, dict] | None:
    """Build the ``{server_name: entry}`` mapping that lands under the
    ``"mcp"`` key in Opencode's ``opencode.json``.

    Returns ``None`` when there's nothing to write — either an empty
    input list, or only unknown-type servers (which are silently
    skipped, matching the existing behavior).
    """
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
        return None
    return config
