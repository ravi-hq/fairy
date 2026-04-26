"""Direct unit tests for `render_claude_mcp_config`.

Mutation-tested. Each test isolates one mutation-killable branch in
the projection from `McpServerSpec` to Claude's `.claude.json` shape:

  - URL servers → ``{"type": "http", "url": ...}`` (NOT
    ``{"type": "stdio"}`` — pins the elif/literal pair)
  - stdio servers → ``{"type": "stdio", "command": ..., "args": ...}``
  - ``headers`` (URL) and ``env`` (stdio) only appear when truthy
  - ``args`` is unconditional — always emitted on stdio entries, even
    when empty (``[]``)
  - Unknown-type servers are skipped silently (no crash, no entry)
  - Empty input → None (caller skips the file write)

Tests are sync, no Django fixtures, no pytest-asyncio, no parametrize —
required so hammett (mutmut's runner, which doesn't load pytest plugins)
can execute them. ``McpServerSpec`` is duck-typed via ``SimpleNamespace``
so this test module doesn't pull Django-dependent imports.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.claude_config import render_claude_mcp_config


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
    """Caller skips the file write when nothing to render. Distinguishes
    a mutant that drops the early-return guard."""
    assert render_claude_mcp_config([]) is None


def test_only_unknown_types_returns_none():
    """All-unknown-type servers also yield None — config dict stays
    empty, the second guard catches it. Without that branch a mutant
    could return ``{}`` and the caller would write an empty mcpServers
    dict."""
    server = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    assert render_claude_mcp_config([server]) is None


# ---------- URL servers ----------


def test_url_server_emits_http_type():
    """URL servers land under ``type: "http"`` — distinguishes a mutant
    that swaps the literal for ``"stdio"`` or another value."""
    cfg = render_claude_mcp_config([_url_server(name="github", url="https://x/mcp")])
    assert cfg == {"github": {"type": "http", "url": "https://x/mcp"}}


def test_url_server_omits_headers_when_empty():
    """An empty-dict headers value is falsy — the headers key must NOT
    be present in the rendered entry. Distinguishes a mutant that drops
    the ``if s.headers:`` guard."""
    cfg = render_claude_mcp_config([_url_server(headers={})])
    assert "headers" not in cfg["srv"]


def test_url_server_includes_headers_when_present():
    """Non-empty headers dict flows through verbatim under the
    ``headers`` key."""
    cfg = render_claude_mcp_config(
        [_url_server(headers={"Authorization": "Bearer X", "X-Trace": "1"})]
    )
    assert cfg["srv"]["headers"] == {"Authorization": "Bearer X", "X-Trace": "1"}


def test_url_server_url_passes_through_unchanged():
    """The ``url`` field is not normalized or rewritten — pinned so a
    refactor that adds URL normalization breaks this test loudly."""
    cfg = render_claude_mcp_config([_url_server(url="https://example.com/mcp/v1?token=abc")])
    assert cfg["srv"]["url"] == "https://example.com/mcp/v1?token=abc"


# ---------- stdio servers ----------


def test_stdio_server_emits_stdio_type_with_command_and_args():
    """stdio servers land under ``type: "stdio"`` with both ``command``
    and ``args`` always present (args may be empty list)."""
    cfg = render_claude_mcp_config([_stdio_server(name="local", command="npx", args=["a"])])
    assert cfg == {"local": {"type": "stdio", "command": "npx", "args": ["a"]}}


def test_stdio_server_with_empty_args_still_includes_args_key():
    """Unlike ``headers`` / ``env``, ``args`` is unconditional — even an
    empty list lands as ``"args": []`` in the rendered entry."""
    cfg = render_claude_mcp_config([_stdio_server(args=[])])
    assert cfg["srv"]["args"] == []


def test_stdio_server_omits_env_when_empty():
    """env is conditional — empty dict means the key is absent."""
    cfg = render_claude_mcp_config([_stdio_server(env={})])
    assert "env" not in cfg["srv"]


def test_stdio_server_includes_env_when_present():
    """Non-empty env dict flows through verbatim under ``env``."""
    cfg = render_claude_mcp_config([_stdio_server(env={"K": "V", "K2": "V2"})])
    assert cfg["srv"]["env"] == {"K": "V", "K2": "V2"}


# ---------- mixed / multi-server ----------


def test_multiple_servers_each_get_their_own_entry():
    """Each server lands under its own ``name`` key. Pins the iteration
    over the input list."""
    cfg = render_claude_mcp_config(
        [
            _url_server(name="alpha", url="https://x/mcp"),
            _stdio_server(name="beta", command="bcmd"),
        ]
    )
    assert set(cfg.keys()) == {"alpha", "beta"}
    assert cfg["alpha"]["type"] == "http"
    assert cfg["beta"]["type"] == "stdio"


def test_unknown_type_in_mixed_list_is_skipped():
    """An unknown-type server alongside known ones is silently dropped;
    the known servers still render. Distinguishes a mutant that adds an
    ``else: raise`` to the type dispatch (which would abort the whole
    render)."""
    unknown = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    known = _url_server(name="real", url="https://x")
    cfg = render_claude_mcp_config([unknown, known])
    assert "ghost" not in cfg
    assert cfg["real"] == {"type": "http", "url": "https://x"}


def test_servers_with_same_name_last_wins():
    """If two specs share a name, the later one overwrites the earlier
    in the output dict — same as plain dict assignment. This isn't a
    spec we want to encourage, but pinning the behavior catches a
    refactor that changes it (e.g. switches to setdefault)."""
    first = _url_server(name="dup", url="https://first")
    second = _stdio_server(name="dup", command="second")
    cfg = render_claude_mcp_config([first, second])
    assert cfg["dup"] == {"type": "stdio", "command": "second", "args": []}
