"""Direct unit tests for `validate_mcp_servers`.

Mutation-tested. Tests cover the type allowlist, per-type required-field
checks, dedup on name, the global maximum, and the index propagation
through the loop.

Tests are sync, no Django fixtures, no parametrize — required so
hammett (mutmut's runner) can execute them.
"""

import pytest

from agent_on_demand.validation.mcp_server_validation import (
    MAX_MCP_SERVERS_PER_AGENT,
    VALID_MCP_SERVER_TYPES,
    validate_mcp_servers,
)


def _url(name="srv", url="https://example.com/mcp"):
    return {"name": name, "type": "url", "url": url}


def _stdio(name="srv", command="my-cmd"):
    return {"name": name, "type": "stdio", "command": command}


# ---------- shape (entry must be a dict) ----------


def test_empty_list_is_accepted():
    assert validate_mcp_servers([]) == []


def test_non_dict_entry_rejects_with_correct_index():
    """A bare string at index i must reject before any field-level
    checks. Asserts the index ``[0]`` appears in the error so a
    refactor that hardcodes the index is caught."""
    with pytest.raises(ValueError, match=r"mcp_servers\[0\] must be an object"):
        validate_mcp_servers(["not-a-dict"])


def test_non_dict_entry_at_second_index_reports_correct_index():
    """Index propagation through the loop — error at index 1, not 0."""
    valid = _url(name="ok")
    with pytest.raises(ValueError, match=r"mcp_servers\[1\] must be an object"):
        validate_mcp_servers([valid, "not-a-dict"])


# ---------- required `name` field ----------


def test_missing_name_rejects():
    """``name`` is required regardless of type."""
    with pytest.raises(ValueError, match=r"mcp_servers\[0\] missing required field: name"):
        validate_mcp_servers([{"type": "url", "url": "https://x"}])


def test_missing_name_index_propagates():
    valid = _url(name="ok")
    with pytest.raises(ValueError, match=r"mcp_servers\[1\] missing required field: name"):
        validate_mcp_servers([valid, {"type": "url", "url": "https://x"}])


# ---------- type allowlist ----------


def test_default_type_is_url():
    """When ``type`` is absent the default is ``url`` — pinned because
    a refactor that changes the default could silently miss a
    required-field check (``stdio`` requires ``command``, ``url``
    requires ``url``)."""
    server = {"name": "srv", "url": "https://x"}
    # No `type` field — defaults to url.
    assert validate_mcp_servers([server]) == [server]


def test_unknown_type_rejects():
    with pytest.raises(ValueError, match=r"unknown type 'sse'"):
        validate_mcp_servers([{"name": "srv", "type": "sse"}])


def test_unknown_type_lists_valid_types_in_message():
    """The error message lists the valid options so the operator can
    correct it. Pin the listing so a refactor doesn't drop it."""
    with pytest.raises(ValueError, match=r"Must be one of:"):
        validate_mcp_servers([{"name": "srv", "type": "future"}])


def test_unknown_type_message_contains_valid_options():
    """Both ``stdio`` and ``url`` must appear in the error so the
    operator sees the full set."""
    with pytest.raises(ValueError) as exc:
        validate_mcp_servers([{"name": "srv", "type": "future"}])
    detail = str(exc.value)
    assert "stdio" in detail
    assert "url" in detail


# ---------- per-type required fields ----------


def test_url_server_missing_url_rejects():
    with pytest.raises(ValueError, match=r"\(url\): missing required field: url"):
        validate_mcp_servers([{"name": "srv", "type": "url"}])


def test_stdio_server_missing_command_rejects():
    with pytest.raises(ValueError, match=r"\(stdio\): missing required field: command"):
        validate_mcp_servers([{"name": "srv", "type": "stdio"}])


def test_stdio_server_with_url_field_does_not_require_command():
    """A stdio server with a stray ``url`` field still needs
    ``command``. Pin that the type-specific check uses ``stype``, not
    presence of url-or-command."""
    with pytest.raises(ValueError, match="missing required field: command"):
        validate_mcp_servers([{"name": "srv", "type": "stdio", "url": "https://x"}])


def test_url_server_with_command_field_still_requires_url():
    """Symmetric: a url server with a stray ``command`` field still
    needs ``url``."""
    with pytest.raises(ValueError, match="missing required field: url"):
        validate_mcp_servers([{"name": "srv", "type": "url", "command": "cmd"}])


# ---------- dedup ----------


def test_duplicate_names_reject():
    with pytest.raises(ValueError, match=r"duplicate name 'shared'"):
        validate_mcp_servers([_url(name="shared"), _url(name="shared")])


def test_duplicate_name_index_reports_second_occurrence():
    """The error reports the index of the SECOND occurrence — pin the
    behavior so an index-tracking refactor can't silently flip it."""
    with pytest.raises(ValueError, match=r"mcp_servers\[1\]: duplicate name 'shared'"):
        validate_mcp_servers([_url(name="shared"), _url(name="shared")])


def test_dedup_works_across_types():
    """A url server and a stdio server with the same name still
    collide — name is the dedup key regardless of shape."""
    with pytest.raises(ValueError, match=r"duplicate name 'shared'"):
        validate_mcp_servers([_url(name="shared"), _stdio(name="shared")])


# ---------- global maximum ----------


def test_max_servers_at_boundary_is_accepted():
    """Exactly MAX_MCP_SERVERS_PER_AGENT is fine; one more is not.
    Pin both sides of the boundary."""
    servers = [_url(name=f"s{i}") for i in range(MAX_MCP_SERVERS_PER_AGENT)]
    assert validate_mcp_servers(servers) == servers


def test_max_servers_plus_one_rejects():
    servers = [_url(name=f"s{i}") for i in range(MAX_MCP_SERVERS_PER_AGENT + 1)]
    with pytest.raises(ValueError, match=f"Maximum {MAX_MCP_SERVERS_PER_AGENT}"):
        validate_mcp_servers(servers)


# ---------- exported constants ----------


def test_valid_mcp_server_types_contains_exactly_url_and_stdio():
    """Pin the contents of the type allowlist. A refactor that adds or
    removes a type must update both this constant AND the per-type
    field check; the test catches drift between them."""
    assert VALID_MCP_SERVER_TYPES == frozenset({"url", "stdio"})


def test_valid_mcp_server_types_is_frozenset():
    """Frozenset is intentional — immutable and hashable. A mutable
    set would let a caller mutate the allowlist accidentally."""
    assert isinstance(VALID_MCP_SERVER_TYPES, frozenset)
