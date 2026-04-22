#!/usr/bin/env python3
"""app.py — an internal dashboard that lets multiple users start and watch
Agent on Demand sessions through a web UI, without each user holding an
AoD token of their own.

Shape:
    Browser  ──HTTP─▶  FastAPI  ──aod-sdk──▶  Agent on Demand
             ◀──SSE──             ◀──SSE────

The dashboard uses a single service token (env `AOD_API_TOKEN`) for all
outbound calls. Your own auth layer would sit in front — this example
assumes the deployment is already behind a VPN, SSO proxy, or similar.

Routes:
    GET  /                          — dashboard HTML
    POST /api/sessions              — start a session {prompt}
    GET  /api/sessions              — list recent sessions (JSON)
    POST /api/sessions/{id}/terminate
    GET  /api/sessions/{id}/stream  — SSE proxy (re-streams /sessions/{id}/stream)

Required env vars:
    AOD_API_URL, AOD_API_TOKEN   (read by aod.AsyncClient)
    AOD_AGENT_ID                  agent every dashboard user shares
Optional:
    PORT            default 8000 — uvicorn bind port
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from aod import AsyncClient, ConflictError, NotFoundError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

AGENT_ID = os.environ["AOD_AGENT_ID"]
PORT = int(os.environ.get("PORT", "8000"))

INDEX_HTML = Path(__file__).parent / "templates" / "index.html"


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    """Keep one AsyncClient for the app's lifetime instead of per-request."""
    async with AsyncClient() as client:
        app_.state.aod = client
        yield


app = FastAPI(lifespan=lifespan)


class StartSession(BaseModel):
    prompt: str


def _client(request: Request) -> AsyncClient:
    return request.app.state.aod


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML.read_text())


@app.get("/api/sessions")
async def list_sessions(request: Request) -> dict:
    sessions = await _client(request).sessions.list()
    return {
        "data": [
            {
                "id": str(s.id),
                "status": s.status,
                "runtime": s.runtime,
                "created_at": s.created_at.isoformat(),
                "turn_count": s.turn_count,
            }
            for s in sessions
        ]
    }


@app.post("/api/sessions", status_code=202)
async def start_session(req: StartSession, request: Request) -> dict:
    ack = await _client(request).sessions.create(agent_id=AGENT_ID, prompt=req.prompt)
    return {"id": str(ack.id), "status": ack.status}


@app.post("/api/sessions/{session_id}/terminate")
async def terminate_session(session_id: str, request: Request) -> dict:
    try:
        ack = await _client(request).sessions.terminate(session_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found") from None
    except ConflictError:
        # Already terminal — idempotent success for the UI.
        return {"id": session_id, "status": "terminated"}
    return {"id": str(ack.id), "status": ack.status}


@app.get("/api/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request) -> StreamingResponse:
    """Proxy the upstream SSE stream straight through to the browser.

    The browser's `EventSource` connects here, not to AoD directly — so the
    AoD token never leaves the server.
    """
    client = _client(request)

    async def generator() -> AsyncIterator[bytes]:
        try:
            async with client.sessions.stream(session_id) as events:
                async for event in events:
                    payload = {"type": event.type, "id": event.id, **event.extra}
                    yield f"data: {json.dumps(payload)}\n\n".encode()
        except NotFoundError:
            yield b'data: {"type":"error","message":"session not found"}\n\n'

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables Nginx response buffering
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
