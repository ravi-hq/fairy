from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from aod import Client


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("AOD_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="Missing API token"):
        Client(base_url="http://mock")


def test_env_fallbacks(monkeypatch):
    monkeypatch.setenv("AOD_API_URL", "http://from-env")
    monkeypatch.setenv("AOD_API_TOKEN", "aod_from_env")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"}))
    client = Client(transport=transport)
    assert client.health() == {"status": "ok"}


def test_health(client, server):
    server.json("GET", "/health", 200, {"status": "ok"})
    assert client.health() == {"status": "ok"}


def test_context_manager_closes(server):
    transport = httpx.MockTransport(server.handle)
    server.json("GET", "/health", 200, {"status": "ok"})
    with Client(base_url="http://mock", token="aod_test", transport=transport) as c:
        assert c.health() == {"status": "ok"}


async def test_async_end_to_end(async_client, server, make_agent):
    server.json("GET", "/health", 200, {"status": "ok"})
    server.json("GET", "/agents", 200, {"data": [make_agent(name="async")]})
    async with async_client as c:
        assert await c.health() == {"status": "ok"}
        agents = await c.agents.list()
        assert agents[0].name == "async"


async def test_async_stream(async_client, server):
    sid = str(uuid4())

    def responder(request):
        body = b'data: {"type":"start","session_id":"' + sid.encode() + b'"}\n\n'
        body += b'data: {"type":"output","id":1,"stream":"stdout","data":"hi"}\n\n'
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    server.register("GET", f"/sessions/{sid}/stream", responder)

    collected = []
    async with async_client as c:
        async with c.sessions.stream(sid) as events:
            async for event in events:
                collected.append(event.type)

    assert collected == ["start", "output"]
