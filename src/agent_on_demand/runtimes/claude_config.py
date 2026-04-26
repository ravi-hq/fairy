"""Build Claude's MCP-server config dict from a list of `McpServerSpec`.

Extracted from `runtimes/claude.py` so the mapping logic can be
mutation-tested in isolation. The original `ClaudeRuntime.write_config`
keeps the JSON-encode + sprite filesystem write; this module is pure
(list of specs in, dict out, no I/O).

Claude's MCP config is permissive compared to Codex — no auth-shape
validation, no rejection of unknown headers — so the rendering is
mostly a 1:1 spec-to-entry projection. The mutmut value here is
pinning the projection itself:

  - ``url``-shaped entries → ``type: http`` with ``url``
    (not ``"command"``).
  - ``stdio``-shaped entries → ``type: stdio`` with ``command``
    and ``args``.
  - ``headers`` (URL) and ``env`` (stdio) appear only when truthy.
  - ``args`` is *unconditional* — emitted on every stdio entry even
    when empty (``[]``).
  - Unknown-type servers are silently skipped.

Each is the kind of thing a refactor can quietly swap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec


def render_claude_mcp_config(mcp_servers: list[McpServerSpec]) -> dict[str, dict] | None:
    """Build the ``{server_name: entry}`` mapping that lands under the
    ``"mcpServers"`` key in Claude's ``.claude.json``.

    Returns ``None`` when there's nothing to write — either an empty
    input list, or only unknown-type servers (which are silently
    skipped). The caller is responsible for skipping the file write in
    that case.
    """
    config: dict[str, dict] = {}
    for s in mcp_servers:
        if s.type == "url":
            entry: dict = {"type": "http", "url": s.url}
            if s.headers:
                entry["headers"] = s.headers
            config[s.name] = entry
        elif s.type == "stdio":
            entry = {"type": "stdio", "command": s.command, "args": s.args}
            if s.env:
                entry["env"] = s.env
            config[s.name] = entry
    if not config:
        return None
    return config
