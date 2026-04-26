"""Render Codex's MCP-server config (TOML) for a list of `McpServerSpec`.

Extracted from `runtimes/codex.py` so the rendering logic — including
the bearer-token validation that Codex's TOML schema demands — can be
mutation-tested in isolation. The original `CodexRuntime.write_config`
keeps the sprite filesystem write; this module is pure (string in,
string out, raises on bad input).

Codex's MCP support is unusually picky:

  - URL servers may carry exactly one header form,
    ``Authorization: Bearer ${ENV_VAR}``. Anything else (a literal
    bearer secret, a non-Authorization header) raises `ProvisionError`
    rather than silently dropping the value.
  - stdio servers accept ``command``, optional ``args`` (TOML array of
    strings), optional ``env`` (TOML table).

The validation is the kind of thing a refactor can quietly weaken (e.g.
swap ``key.lower() == "authorization"`` for ``key == "authorization"``,
or drop the literal-bearer rejection branch). Mutmut catches those.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_on_demand.session_service.errors import ProvisionError

if TYPE_CHECKING:
    from agent_on_demand.session_service.specs import McpServerSpec


def render_codex_mcp_config(mcp_servers: list[McpServerSpec]) -> str:
    """Build the TOML body for ``/home/sprite/.codex/config.toml``.

    Returns the empty string when ``mcp_servers`` is empty (caller is
    responsible for skipping the file write in that case).
    """
    if not mcp_servers:
        return ""
    lines: list[str] = []
    for s in mcp_servers:
        lines.append(f"[mcp_servers.{s.name}]")
        if s.type == "url":
            lines.append(f'url = "{s.url}"')
            for key, val in s.headers.items():
                if key.lower() == "authorization" and val.startswith("Bearer "):
                    token = val.removeprefix("Bearer ").strip()
                    if token.startswith("${") and token.endswith("}"):
                        lines.append(f'bearer_token_env_var = "{token[2:-1]}"')
                    else:
                        raise ProvisionError(
                            f"MCP server {s.name!r}: Codex only supports env-var Bearer "
                            f"tokens (e.g. 'Bearer ${{MY_TOKEN}}'); got a literal value",
                            stage="write_config",
                        )
                else:
                    raise ProvisionError(
                        f"MCP server {s.name!r}: Codex config does not support header "
                        f"{key!r}; only 'Authorization: Bearer ${{ENV_VAR}}' is supported",
                        stage="write_config",
                    )
            lines.append("required = true")
        elif s.type == "stdio":
            lines.append(f'command = "{s.command}"')
            if s.args:
                args_str = ", ".join(f'"{a}"' for a in s.args)
                lines.append(f"args = [{args_str}]")
            if s.env:
                lines.append(f"[mcp_servers.{s.name}.env]")
                for key, val in s.env.items():
                    lines.append(f'{key} = "{val}"')
        else:
            # Codex is strict about everything else (bearer tokens, header
            # shapes); be strict here too rather than emitting a bare
            # section header for an unrecognized type. The API validator
            # (VALID_MCP_SERVER_TYPES in views/agents.py) already blocks
            # anything other than url/stdio at request time, so this
            # branch only fires on a future spec drift.
            raise ProvisionError(
                f"MCP server {s.name!r}: unsupported type {s.type!r}; "
                f"Codex config supports only 'url' and 'stdio'",
                stage="write_config",
            )
        lines.append("")
    return "\n".join(lines)
