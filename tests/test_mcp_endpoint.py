"""Unit tests for the /test-mcp server used by the MCP e2e matrix."""

import json

import pytest
from django.test import Client


@pytest.fixture
def anon_client():
    return Client()


@pytest.fixture
def debug_on(settings):
    """Force DEBUG=True so the /test-mcp view's enable check passes."""
    settings.DEBUG = True
    settings.TESTING = False
    return settings


def _rpc(client: Client, body: dict, **extra) -> dict:
    resp = client.post(
        "/test-mcp",
        data=json.dumps(body),
        content_type="application/json",
        **extra,
    )
    assert resp.status_code == 200, resp.content
    return resp.json()


class TestTestMcpTools:
    def test_initialize(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test", "version": "1"},
                "capabilities": {},
            },
        })
        assert result["id"] == 1
        assert result["result"]["protocolVersion"] == "2024-11-05"
        assert result["result"]["serverInfo"]["name"] == "fairy-test-mcp"
        assert "tools" in result["result"]["capabilities"]

    def test_tools_list_includes_three_tools(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
        })
        names = {t["name"] for t in result["result"]["tools"]}
        assert names == {"signal_tool", "echo", "dangerous_tool"}

    def test_signal_tool_emits_prefix(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "signal_tool", "arguments": {"token": "OK"}},
        })
        assert result["result"]["content"][0]["text"] == "MCP_SIGNAL_OK"

    def test_echo_returns_msg(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "echo", "arguments": {"msg": "hello"}},
        })
        assert result["result"]["content"][0]["text"] == "hello"

    def test_dangerous_tool_returns_sentinel(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "dangerous_tool", "arguments": {}},
        })
        assert result["result"]["content"][0]["text"] == "SHOULD_NOT_BE_CALLED"

    def test_unknown_method_returns_error(self, anon_client: Client, debug_on):
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 6, "method": "not_a_method",
        })
        assert result["error"]["code"] == -32601

    def test_notification_returns_202(self, anon_client: Client, debug_on):
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({
                "jsonrpc": "2.0", "method": "notifications/initialized",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 202

    def test_invalid_json_returns_parse_error(self, anon_client: Client, debug_on):
        resp = anon_client.post(
            "/test-mcp",
            data="not-json",
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32700


class TestTestMcpGated:
    """The route is always registered; the view's _endpoint_enabled check gates it."""

    def test_returns_403_when_debug_and_testing_off(
        self, anon_client: Client, settings,
    ):
        settings.DEBUG = False
        settings.TESTING = False
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"]

    def test_enabled_when_testing_true(self, anon_client: Client, settings):
        settings.DEBUG = False
        settings.TESTING = True
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200


class TestTestMcpAuth:
    def test_no_token_env_allows_unauth(
        self, anon_client: Client, debug_on, monkeypatch,
    ):
        monkeypatch.delenv("MCP_TEST_TOKEN", raising=False)
        result = _rpc(anon_client, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
        })
        assert "result" in result

    def test_token_env_requires_bearer(
        self, anon_client: Client, debug_on, monkeypatch,
    ):
        monkeypatch.setenv("MCP_TEST_TOKEN", "secret")
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer secret",
        )
        assert resp.status_code == 200

    def test_token_env_rejects_wrong_token(
        self, anon_client: Client, debug_on, monkeypatch,
    ):
        monkeypatch.setenv("MCP_TEST_TOKEN", "secret")
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer wrong",
        )
        assert resp.status_code == 401

    def test_token_env_rejects_missing_auth(
        self, anon_client: Client, debug_on, monkeypatch,
    ):
        monkeypatch.setenv("MCP_TEST_TOKEN", "secret")
        resp = anon_client.post(
            "/test-mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            content_type="application/json",
        )
        assert resp.status_code == 401
