"""
Changelog Writer — concurrent agent fan-out demo
FastAPI app: one git log input, three simultaneous agent sessions streaming
a CHANGELOG entry, blog post intro, and tweet thread in parallel.
"""

import asyncio
import json
import random
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from release_data import RELEASES

app = FastAPI(title="Changelog Writer — Concurrent Agents Demo")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Mock concurrent agent streams
# ---------------------------------------------------------------------------

# Each format streams at a slightly different base speed so all three panels
# visibly fill at the same time but feel independent.
FORMAT_DELAYS = {
    "changelog": 0.18,
    "blogpost": 0.22,
    "tweetthread": 0.20,
}


async def mock_format_stream(release_id: str, fmt: str) -> AsyncIterator[dict]:
    """
    Simulate one agent session producing a single output format.
    Yields events tagged with `format` so the multiplexer can route them.
    """
    release = RELEASES.get(release_id)
    if not release:
        yield {"format": fmt, "type": "error", "data": f"Unknown release: {release_id}"}
        return

    lines: list[str] = release[fmt]

    # Brief startup pause — feels like an agent session spinning up
    await asyncio.sleep(0.1 + random.random() * 0.2)
    yield {"format": fmt, "type": "stage", "data": "starting"}

    for line in lines:
        await asyncio.sleep(FORMAT_DELAYS[fmt] + random.random() * 0.25)
        yield {"format": fmt, "type": "output", "data": line + "\n"}

    yield {"format": fmt, "type": "exit", "data": "0"}


# ---------------------------------------------------------------------------
# Multiplexed SSE endpoint
# ---------------------------------------------------------------------------

@app.get("/changelog/stream/{release_id}")
async def stream_release(release_id: str, request: Request):
    """
    Single SSE endpoint that fans out three concurrent agent sessions —
    one per output format — and multiplexes all events onto one stream.
    Each SSE event carries a `format` field so the client routes it to
    the correct panel.
    """
    if release_id not in RELEASES:
        raise HTTPException(status_code=404, detail=f"Release '{release_id}' not found")

    queue: asyncio.Queue = asyncio.Queue()
    formats = ["changelog", "blogpost", "tweetthread"]

    async def run_format(fmt: str):
        async for event in mock_format_stream(release_id, fmt):
            await queue.put(event)
        await queue.put({"format": fmt, "type": "done"})

    async def event_generator():
        # Kick off all three format agents concurrently
        tasks = [asyncio.create_task(run_format(f)) for f in formats]
        done_count = 0

        try:
            while done_count < len(formats):
                if await request.is_disconnected():
                    for t in tasks:
                        t.cancel()
                    return

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
                    continue

                if event["type"] == "done":
                    done_count += 1
                else:
                    yield {"data": json.dumps(event)}

            # Drain tasks cleanly
            for t in tasks:
                await t

        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/changelog/releases")
async def list_releases():
    """Return the list of pre-loaded sample releases."""
    return {
        "releases": [
            {"id": r["id"], "label": r["label"], "git_log": r["git_log"]}
            for r in RELEASES.values()
        ]
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()
