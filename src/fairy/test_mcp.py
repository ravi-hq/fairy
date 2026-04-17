"""Deterministic MCP test server for e2e MCP enforcement tests.

Implements the MCP Streamable HTTP protocol (JSON-RPC over POST) with three
tools: signal_tool, echo, dangerous_tool. Gated behind DEBUG or FAIRY_TESTING
so it's never live in production.

Do NOT rely on this endpoint from any non-test client.
"""

import json
import os

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "fairy-test-mcp", "version": "1.0.0"}

TOOLS = [
    {
        "name": "signal_tool",
        "description": (
            "Returns 'MCP_SIGNAL_<token>'. Used by e2e tests to confirm the MCP "
            "tool was actually invoked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"token": {"type": "string"}},
            "required": ["token"],
        },
    },
    {
        "name": "echo",
        "description": "Echoes the input message back verbatim.",
        "inputSchema": {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
    },
    {
        "name": "dangerous_tool",
        "description": (
            "Returns 'SHOULD_NOT_BE_CALLED'. Used by e2e tests to verify deny "
            "paths — if this response appears in output, the deny failed."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "signal_tool":
        token = arguments.get("token", "")
        return {"content": [{"type": "text", "text": f"MCP_SIGNAL_{token}"}]}
    if name == "echo":
        return {"content": [{"type": "text", "text": str(arguments.get("msg", ""))}]}
    if name == "dangerous_tool":
        return {"content": [{"type": "text", "text": "SHOULD_NOT_BE_CALLED"}]}
    return {
        "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
        "isError": True,
    }


def _endpoint_enabled() -> bool:
    return settings.DEBUG or getattr(settings, "TESTING", False)


def _check_auth(request) -> bool:
    expected = os.environ.get("MCP_TEST_TOKEN")
    if not expected:
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return auth.removeprefix("Bearer ").strip() == expected


def _handle_request(req: dict) -> dict | None:
    """Dispatch a single JSON-RPC request. Returns None for notifications."""
    method = req.get("method")
    req_id = req.get("id")

    if req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        return {"jsonrpc": "2.0", "id": req_id, "result": _call_tool(tool_name, args)}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


@csrf_exempt
@require_POST
def mcp_streamable_http(request):
    if not _endpoint_enabled():
        return JsonResponse({"detail": "test-mcp disabled"}, status=403)
    if not _check_auth(request):
        return JsonResponse({"detail": "unauthorized"}, status=401)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status=400,
        )

    if isinstance(body, list):
        responses = [r for r in (_handle_request(req) for req in body) if r is not None]
        if not responses:
            return HttpResponse(status=202)
        return JsonResponse(responses, safe=False)

    response = _handle_request(body)
    if response is None:
        return HttpResponse(status=202)
    return JsonResponse(response)
