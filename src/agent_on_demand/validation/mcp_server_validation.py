"""Validate the ``mcp_servers`` field on agent create/update requests.

Extracted from `views/agents.py` so the validation logic — type
allowlist, per-type required-field checks, dedup, and the global
maximum — can be mutation-tested in isolation. The view layer keeps
the wiring (request parsing, ValueError → 422 mapping); this module
is pure (dict-list in, validated dict-list out, raises ``ValueError``
on bad input).

Two MCP server shapes:

  - **url**: ``{"name": ..., "type": "url", "url": ..., "headers": ...?}``
    — connects via HTTP/SSE to a remote MCP server.
  - **stdio**: ``{"name": ..., "type": "stdio", "command": ...,
    "args": ...?, "env": ...?}`` — spawns a local process and speaks
    MCP over stdin/stdout.

The ``type`` field is required (defaulted to ``"url"`` if absent), the
``name`` field is required, and per-type extra fields (``url`` /
``command``) must be present. The actual rendering of these into each
runtime's config file lives in ``runtimes/{codex,claude,gemini,opencode}_config.py``;
those modules trust the validation here.
"""

from __future__ import annotations


VALID_MCP_SERVER_TYPES = frozenset({"url", "stdio"})

# Maximum MCP servers per agent. Each server registers itself with the
# runtime CLI at session start, and large lists slow startup time
# linearly; cap at a number that's generous for real configurations
# while preventing accidental DOS via a runaway list.
MAX_MCP_SERVERS_PER_AGENT = 20


def validate_mcp_servers(servers: list) -> list:
    """Validate every entry in ``servers`` and return it unchanged on
    success. Raises ``ValueError`` on the first violation; the caller
    in views/agents.py turns that into a 422 with the message verbatim.
    """
    names = set()
    for i, server in enumerate(servers):
        if not isinstance(server, dict):
            raise ValueError(f"mcp_servers[{i}] must be an object")
        if "name" not in server:
            raise ValueError(f"mcp_servers[{i}] missing required field: name")
        stype = server.get("type", "url")
        if stype not in VALID_MCP_SERVER_TYPES:
            raise ValueError(
                f"mcp_servers[{i}]: unknown type {stype!r}. "
                f"Must be one of: {sorted(VALID_MCP_SERVER_TYPES)}"
            )
        if stype == "url" and "url" not in server:
            raise ValueError(f"mcp_servers[{i}] (url): missing required field: url")
        if stype == "stdio" and "command" not in server:
            raise ValueError(f"mcp_servers[{i}] (stdio): missing required field: command")
        if server["name"] in names:
            raise ValueError(f"mcp_servers[{i}]: duplicate name {server['name']!r}")
        names.add(server["name"])
    if len(servers) > MAX_MCP_SERVERS_PER_AGENT:
        raise ValueError(f"Maximum {MAX_MCP_SERVERS_PER_AGENT} MCP servers per agent")
    return servers
