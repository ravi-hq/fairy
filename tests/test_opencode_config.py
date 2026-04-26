"""Direct unit tests for `render_opencode_mcp_config`.

Mutation-tested. Each test isolates one mutation-killable branch.
Opencode's projection has the most quirks of any runtime — five
distinct naming differences from other runtimes — and each is the kind
of thing a refactor can quietly "normalize" back to a sibling
runtime's shape:

  - URL servers → ``type: "remote"`` (NOT ``"http"`` like Claude,
    NOT ``"url"`` like Codex)
  - stdio servers → ``type: "local"`` (NOT ``"stdio"``)
  - stdio ``command`` is a *merged array* ``[cmd, *args]`` — there
    is no separate ``args`` field
  - env table key is ``environment`` (NOT ``env``)
  - Every entry carries ``enabled: true`` — drop this and the server
    is registered but won't be invoked

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them. ``McpServerSpec`` is
duck-typed via ``SimpleNamespace``.
"""

from types import SimpleNamespace

from agent_on_demand.runtimes.opencode_config import render_opencode_mcp_config


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
    assert render_opencode_mcp_config([]) is None


def test_only_unknown_types_returns_none():
    """Unknown types skip via ``else: continue``; if every server is
    unknown, ``config`` stays empty and the final guard returns None."""
    server = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    assert render_opencode_mcp_config([server]) is None


# ---------- URL servers: type "remote" + enabled ----------


def test_url_server_type_is_remote_not_http_or_stdio():
    """Opencode calls URL-shaped MCP servers ``"remote"`` (not
    ``"http"`` like Claude, ``"stdio"`` like other runtimes' string).
    Pinned because a refactor that "normalizes" runtime configs is
    likely to swap this back to a sibling's literal."""
    cfg = render_opencode_mcp_config([_url_server(url="https://x/mcp")])
    assert cfg["srv"]["type"] == "remote"


def test_url_server_url_field_is_url_not_httpUrl():
    """Unlike Gemini (``httpUrl``), opencode uses the field name
    ``url``. Pinning the difference."""
    cfg = render_opencode_mcp_config([_url_server(url="https://x/mcp")])
    assert cfg["srv"]["url"] == "https://x/mcp"


def test_url_server_carries_enabled_true():
    """Without ``enabled: true`` the server is registered but won't
    actually be invoked at agent runtime."""
    cfg = render_opencode_mcp_config([_url_server()])
    assert cfg["srv"]["enabled"] is True


def test_url_server_omits_headers_when_empty():
    cfg = render_opencode_mcp_config([_url_server(headers={})])
    assert "headers" not in cfg["srv"]


def test_url_server_includes_headers_when_present():
    cfg = render_opencode_mcp_config([_url_server(headers={"X": "Y"})])
    assert cfg["srv"]["headers"] == {"X": "Y"}


# ---------- stdio servers: type "local", merged command, environment key ----------


def test_stdio_server_type_is_local_not_stdio():
    """Opencode calls stdio-shaped MCP servers ``"local"``."""
    cfg = render_opencode_mcp_config([_stdio_server()])
    assert cfg["srv"]["type"] == "local"


def test_stdio_server_command_merges_command_and_args_into_one_array():
    """Unlike Claude/Gemini (separate ``command`` string + ``args``
    list), opencode merges them into one array under ``command``.
    There is NO separate ``args`` field. Pinning the array shape."""
    cfg = render_opencode_mcp_config([_stdio_server(command="npx", args=["-y", "@some/pkg"])])
    assert cfg["srv"]["command"] == ["npx", "-y", "@some/pkg"]
    assert "args" not in cfg["srv"]


def test_stdio_server_with_no_args_command_is_single_element_list():
    """Even with empty args, ``command`` is a list, not a bare string —
    consistent shape so the agent's parser doesn't need to branch."""
    cfg = render_opencode_mcp_config([_stdio_server(command="cmd", args=[])])
    assert cfg["srv"]["command"] == ["cmd"]


def test_stdio_server_carries_enabled_true():
    cfg = render_opencode_mcp_config([_stdio_server()])
    assert cfg["srv"]["enabled"] is True


def test_stdio_server_env_lands_under_environment_key_not_env():
    """Opencode's env table key is ``environment``, not ``env``. Pinned
    because a refactor that "harmonizes" naming with the other runtimes
    would silently break opencode's auth via env."""
    cfg = render_opencode_mcp_config([_stdio_server(env={"K": "V"})])
    assert cfg["srv"]["environment"] == {"K": "V"}
    assert "env" not in cfg["srv"]


def test_stdio_server_omits_environment_when_empty():
    cfg = render_opencode_mcp_config([_stdio_server(env={})])
    assert "environment" not in cfg["srv"]


# ---------- mixed / multi-server ----------


def test_multiple_servers_each_get_their_own_entry():
    cfg = render_opencode_mcp_config(
        [
            _url_server(name="alpha", url="https://x"),
            _stdio_server(name="beta", command="bcmd"),
        ]
    )
    assert set(cfg.keys()) == {"alpha", "beta"}
    assert cfg["alpha"]["type"] == "remote"
    assert cfg["beta"]["type"] == "local"


def test_unknown_type_in_mixed_list_is_skipped():
    """The explicit ``else: continue`` keeps unknown types out of the
    config without crashing the render. Same permissive policy as
    Claude/Gemini, but opencode is the only runtime with the explicit
    branch — pinned so a refactor doesn't "clean up" the dead else."""
    unknown = SimpleNamespace(
        name="ghost", type="future-shape", url="", headers={}, command="", args=[], env={}
    )
    known = _url_server(name="real", url="https://x")
    cfg = render_opencode_mcp_config([unknown, known])
    assert "ghost" not in cfg
    assert cfg["real"]["url"] == "https://x"
