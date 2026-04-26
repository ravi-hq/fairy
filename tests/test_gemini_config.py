"""Direct unit tests for `render_gemini_mcp_config`.

Mutation-tested. Each test isolates one mutation-killable branch:

  - URL servers → ``{"httpUrl": ..., "trust": true}`` (NOT ``"url"``,
    NOT ``"trust": false``)
  - stdio servers → ``{"command": ..., "args": ..., "trust": true}``
  - Optional ``headers`` / ``env`` only appear when truthy
  - Unknown-type servers are skipped silently
  - Empty input → None (caller skips the file write)

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner, which doesn't load pytest plugins) can
execute them. ``McpServerSpec`` is duck-typed via ``SimpleNamespace``
so this module doesn't pull Django-dependent imports.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.gemini_config import render_gemini_mcp_config


def _url_server(name="srv", url="https://x/mcp", headers=None):
    # ``headers if headers is not None else {}`` rather than
    # ``headers or {}`` — the falsy form treats an empty-dict input the
    # same as None, which masks intent in tests like
    # test_url_server_omits_headers_when_empty that pass {} explicitly.
    return SimpleNamespace(
        name=name,
        type="url",
        url=url,
        headers=headers if headers is not None else {},
        command="",
        args=[],
        env={},
    )


def _stdio_server(name="srv", command="my-cmd", args=None, env=None):
    return SimpleNamespace(
        name=name,
        type="stdio",
        url="",
        headers={},
        command=command,
        args=args if args is not None else [],
        env=env if env is not None else {},
    )


# ---------- empty-input handling ----------


def test_empty_list_returns_none():
    """Caller skips the file write when nothing to render."""
    assert render_gemini_mcp_config([]) is None


def test_only_unknown_types_returns_none():
    """All-unknown-type servers also yield None — config dict stays
    empty, the second guard catches it."""
    server = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    assert render_gemini_mcp_config([server]) is None


# ---------- URL servers: the httpUrl quirk + trust flag ----------


def test_url_server_uses_httpUrl_field_not_url():
    """Gemini's MCP config calls the URL field ``httpUrl`` (not ``url``
    like Codex/Claude). A refactor that "normalizes" this back to
    ``url`` would silently break MCP for every Gemini session — pin it."""
    cfg = render_gemini_mcp_config([_url_server(url="https://x/mcp")])
    entry = cfg["srv"]
    assert "httpUrl" in entry
    assert entry["httpUrl"] == "https://x/mcp"
    assert "url" not in entry


def test_url_server_carries_trust_true():
    """``trust: True`` authorizes the agent to invoke server tools
    without per-call confirmation. Drop this and every tool call
    silently starts prompting — broken in headless mode."""
    cfg = render_gemini_mcp_config([_url_server()])
    assert cfg["srv"]["trust"] is True


def test_url_server_omits_headers_when_empty():
    cfg = render_gemini_mcp_config([_url_server(headers={})])
    assert "headers" not in cfg["srv"]


def test_url_server_includes_headers_when_present():
    cfg = render_gemini_mcp_config(
        [_url_server(headers={"Authorization": "Bearer X", "X-Trace": "1"})]
    )
    assert cfg["srv"]["headers"] == {"Authorization": "Bearer X", "X-Trace": "1"}


# ---------- stdio servers: also need trust ----------


def test_stdio_server_carries_trust_true():
    """Same trust requirement applies to stdio servers."""
    cfg = render_gemini_mcp_config([_stdio_server()])
    assert cfg["srv"]["trust"] is True


def test_stdio_server_emits_command_and_args():
    """stdio servers carry both ``command`` and ``args``; ``args`` is
    unconditional (even an empty list lands in the config)."""
    cfg = render_gemini_mcp_config([_stdio_server(command="npx", args=["-y", "pkg"])])
    assert cfg["srv"]["command"] == "npx"
    assert cfg["srv"]["args"] == ["-y", "pkg"]


def test_stdio_server_with_empty_args_still_includes_args_key():
    cfg = render_gemini_mcp_config([_stdio_server(args=[])])
    assert "args" in cfg["srv"]
    assert cfg["srv"]["args"] == []


def test_stdio_server_omits_env_when_empty():
    cfg = render_gemini_mcp_config([_stdio_server(env={})])
    assert "env" not in cfg["srv"]


def test_stdio_server_includes_env_when_present():
    cfg = render_gemini_mcp_config([_stdio_server(env={"K": "V"})])
    assert cfg["srv"]["env"] == {"K": "V"}


# ---------- mixed / multi-server ----------


def test_multiple_servers_each_get_their_own_entry():
    """Pins iteration over the input list."""
    cfg = render_gemini_mcp_config(
        [
            _url_server(name="alpha", url="https://x/mcp"),
            _stdio_server(name="beta", command="bcmd"),
        ]
    )
    assert set(cfg.keys()) == {"alpha", "beta"}
    assert "httpUrl" in cfg["alpha"]
    assert "command" in cfg["beta"]


def test_unknown_type_in_mixed_list_is_skipped():
    """Unlike Codex (which now raises on unknown types), Gemini follows
    Claude's permissive pattern — unknown types silently drop."""
    unknown = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    known = _url_server(name="real", url="https://x")
    cfg = render_gemini_mcp_config([unknown, known])
    assert "ghost" not in cfg
    assert cfg["real"]["httpUrl"] == "https://x"
