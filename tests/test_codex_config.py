"""Direct unit tests for `render_codex_mcp_config`.

Mutation-tested. Each test isolates one mutation-killable branch in the
TOML rendering or in Codex's bearer-token validation:

  - URL section header / `url = "..."` line
  - Authorization header detection (case-insensitive, "Bearer " prefix)
  - Env-var bearer-token shape: ``${NAME}`` → ``bearer_token_env_var = "NAME"``
  - Literal bearer token raises (rather than landing in the config)
  - Non-Authorization header raises
  - URL servers always emit ``required = true``
  - stdio command / args / env rendering
  - Empty input returns empty string

Tests are sync, no Django fixtures, no pytest-asyncio, no parametrize —
required so hammett (mutmut's runner, which doesn't load pytest plugins)
can execute them. ``McpServerSpec`` is duck-typed via ``SimpleNamespace``
so this test module doesn't pull Django-dependent imports.
"""

from types import SimpleNamespace

import pytest

from agent_on_demand.runtimes.codex_config import render_codex_mcp_config
from agent_on_demand.session_service.errors import ProvisionError


def _url_server(name="srv", url="https://mcp.example.com/mcp", headers=None):
    return SimpleNamespace(
        name=name,
        type="url",
        url=url,
        headers=headers or {},
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
        args=args or [],
        env=env or {},
    )


# ---------- empty-input handling ----------


def test_empty_list_returns_empty_string():
    """Caller skips the file write when the rendered body is empty.
    Distinguishes a mutant that drops the early-return guard."""
    assert render_codex_mcp_config([]) == ""


# ---------- URL servers ----------


def test_url_server_emits_section_header_and_url_line():
    """Section header carries the server name; `url = "..."` is the
    bare URL value. Pins both lines in the right order."""
    body = render_codex_mcp_config([_url_server(name="github", url="https://x/mcp")])
    lines = body.splitlines()
    assert lines[0] == "[mcp_servers.github]"
    assert lines[1] == 'url = "https://x/mcp"'


def test_url_server_with_no_headers_emits_required_true():
    """Every URL server gets ``required = true`` regardless of headers.
    Asserts the value as a *whole line* (not a substring) so a mutant
    that wraps the literal in extra characters — e.g. mutmut's
    ``"XXrequired = trueXX"`` rewrite — still fails the test."""
    body = render_codex_mcp_config([_url_server()])
    assert "required = true" in body.splitlines()


def test_url_server_section_uses_server_name_not_constant():
    """The section header interpolates `s.name`. A mutant that hardcodes
    the section name would still pass the previous tests; this one
    distinguishes them."""
    body = render_codex_mcp_config([_url_server(name="distinct-name")])
    assert "[mcp_servers.distinct-name]" in body


# ---------- Bearer-token / Authorization header parsing ----------


def test_env_var_bearer_token_emits_bearer_token_env_var():
    """The env-var bearer form ``Bearer ${NAME}`` writes
    ``bearer_token_env_var = "NAME"`` (without the ``${}`` wrapper)."""
    server = _url_server(headers={"Authorization": "Bearer ${MY_TOKEN}"})
    body = render_codex_mcp_config([server])
    assert 'bearer_token_env_var = "MY_TOKEN"' in body


def test_authorization_header_match_is_case_insensitive():
    """``key.lower() == "authorization"`` — uppercase / mixed-case keys
    must still hit the bearer path. Distinguishes a mutant that drops
    the `.lower()` call."""
    server = _url_server(headers={"AUTHORIZATION": "Bearer ${T}"})
    body = render_codex_mcp_config([server])
    assert 'bearer_token_env_var = "T"' in body


def test_literal_bearer_token_raises_provision_error():
    """A literal Bearer secret would land verbatim in the TOML config —
    Codex's schema doesn't accept it. Reject loudly. Distinguishes
    mutants that drop the ``startswith("${")`` / ``endswith("}")``
    check."""
    server = _url_server(headers={"Authorization": "Bearer literal-secret"})
    with pytest.raises(ProvisionError) as exc:
        render_codex_mcp_config([server])
    assert exc.value.stage == "write_config"
    assert "literal value" in str(exc.value)


def test_bearer_token_missing_dollar_brace_raises():
    """``Bearer NAME`` without the ``${...}`` wrapper is the same kind
    of literal-value mistake — also rejected."""
    server = _url_server(headers={"Authorization": "Bearer NAME"})
    with pytest.raises(ProvisionError):
        render_codex_mcp_config([server])


def test_bearer_token_missing_close_brace_raises():
    """``Bearer ${NAME`` (truncated) is also rejected — the
    ``endswith("}")`` check guards against malformed env-var refs that
    would otherwise produce broken TOML."""
    server = _url_server(headers={"Authorization": "Bearer ${NAME"})
    with pytest.raises(ProvisionError):
        render_codex_mcp_config([server])


def test_non_authorization_header_raises():
    """Codex only supports ``Authorization: Bearer ${ENV}``. Any other
    header would silently drop today and break the user's auth flow.
    Distinguishes mutants that drop the rejection branch."""
    server = _url_server(headers={"X-Custom-Header": "value"})
    with pytest.raises(ProvisionError) as exc:
        render_codex_mcp_config([server])
    assert exc.value.stage == "write_config"
    assert "X-Custom-Header" in str(exc.value)


def test_authorization_value_without_bearer_prefix_raises():
    """``Authorization: Basic ...`` (no ``Bearer ``) hits the
    non-Authorization branch. Pins the ``startswith("Bearer ")``
    half of the conjunction — asserts on the specific error message
    so a mutant that swaps ``and`` for ``or`` (which would route this
    through the bearer-token path and raise a "literal value" error
    instead) is distinguishable."""
    server = _url_server(headers={"Authorization": "Basic abc=="})
    with pytest.raises(ProvisionError) as exc:
        render_codex_mcp_config([server])
    assert "does not support header" in str(exc.value)
    assert "'Authorization'" in str(exc.value)


# ---------- stdio servers ----------


def test_stdio_server_emits_command_only_when_no_args_or_env():
    """Minimal stdio server: just ``command = "..."``. Pins that args
    and env tables aren't emitted when empty."""
    body = render_codex_mcp_config([_stdio_server(command="npx")])
    assert 'command = "npx"' in body
    assert "args =" not in body
    assert ".env]" not in body


def test_stdio_server_emits_args_array_when_provided():
    """args render as a TOML array of double-quoted strings, in order."""
    body = render_codex_mcp_config(
        [_stdio_server(args=["-y", "@modelcontextprotocol/server-everything"])]
    )
    assert 'args = ["-y", "@modelcontextprotocol/server-everything"]' in body


def test_stdio_server_emits_env_table_when_provided():
    """env renders as a nested TOML table: ``[mcp_servers.<name>.env]``
    with ``KEY = "value"`` lines underneath."""
    body = render_codex_mcp_config([_stdio_server(name="local", env={"API_KEY": "v"})])
    assert "[mcp_servers.local.env]" in body
    assert 'API_KEY = "v"' in body


def test_stdio_server_emits_both_args_and_env():
    """Both optional sections together — pins they don't accidentally
    suppress each other."""
    body = render_codex_mcp_config(
        [_stdio_server(name="local", args=["-y", "pkg"], env={"K": "V"})]
    )
    assert 'args = ["-y", "pkg"]' in body
    assert "[mcp_servers.local.env]" in body
    assert 'K = "V"' in body


# ---------- multi-server / mixed ----------


def test_multiple_servers_each_get_their_own_section():
    """Two servers → two sections, each with its own header line. Pins
    iteration over the input list."""
    body = render_codex_mcp_config(
        [
            _url_server(name="alpha", headers={"Authorization": "Bearer ${A}"}),
            _stdio_server(name="beta", command="cmd"),
        ]
    )
    assert "[mcp_servers.alpha]" in body
    assert "[mcp_servers.beta]" in body
    assert 'bearer_token_env_var = "A"' in body
    assert 'command = "cmd"' in body


def test_multiple_servers_have_blank_line_between_sections():
    """Each server section ends with an empty line so the rendered TOML
    is readable. The trailing blank line is part of the existing
    behavior — pinned so a refactor doesn't quietly drop it."""
    body = render_codex_mcp_config(
        [_stdio_server(name="alpha", command="a"), _stdio_server(name="beta", command="b")]
    )
    # Between section "alpha" and section "beta" there is a blank line.
    alpha_idx = body.index("[mcp_servers.alpha]")
    beta_idx = body.index("[mcp_servers.beta]")
    between = body[alpha_idx:beta_idx]
    assert "\n\n" in between


def test_first_failing_server_short_circuits_the_render():
    """A server that raises ProvisionError aborts the render — later
    servers don't get evaluated. Pins that we don't accumulate partial
    output past a validation failure (no swallowing the exception, no
    moving on to the next server). The error message references the
    bad server explicitly, never the good one — confirming the loop
    exited rather than fell through and joined both."""
    bad = _url_server(name="bad", headers={"Authorization": "Bearer literal"})
    good = _stdio_server(name="good", command="ok")
    with pytest.raises(ProvisionError) as exc:
        render_codex_mcp_config([bad, good])
    assert "'bad'" in str(exc.value)
    assert "'good'" not in str(exc.value)


def test_unknown_server_type_raises():
    """The API validator blocks anything other than url/stdio at request
    time, so this branch only fires on a future spec drift. Codex is
    strict about everything else (bearer tokens, header shapes); be
    strict here too rather than emitting a bare section header for an
    unrecognized type."""
    server = SimpleNamespace(
        name="ghost",
        type="future-shape",
        url="",
        headers={},
        command="",
        args=[],
        env={},
    )
    with pytest.raises(ProvisionError) as exc:
        render_codex_mcp_config([server])
    assert exc.value.stage == "write_config"
    assert "unsupported type" in str(exc.value)
    assert "'future-shape'" in str(exc.value)
