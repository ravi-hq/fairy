"""
PR Reviewer demo — FastAPI app.

Routes
------
GET  /reviews/prs              list the three mock PRs
GET  /reviews/stream/{pr_id}   SSE stream of the review
GET  /                         serve index.html
"""

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from review_data import PULL_REQUESTS, REVIEW_DATA

app = FastAPI(title="PR Reviewer Demo")

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


# ---------------------------------------------------------------------------
# PR listing
# ---------------------------------------------------------------------------


@app.get("/reviews/prs")
async def list_prs():
    return [
        {
            "id": pr["id"],
            "number": pr["number"],
            "title": pr["title"],
            "description": pr["description"],
            "author": pr["author"],
            "language": pr["language"],
            "files_changed": pr["files_changed"],
            "additions": pr["additions"],
            "deletions": pr["deletions"],
            "diff": pr["diff"],
        }
        for pr in PULL_REQUESTS.values()
    ]


# ---------------------------------------------------------------------------
# Streaming review — mock AoD client
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    kind: str  # "stage" | "output" | "exit"
    data: str


async def mock_review_stream(pr_id: str) -> AsyncIterator[StreamEvent]:
    """Simulate a streaming agent session with realistic per-line delays."""
    review = REVIEW_DATA.get(pr_id)
    if review is None:
        yield StreamEvent("exit", "1")
        return

    # Provisioning stages
    for stage in ["create_sprite", "runtime_start"]:
        await asyncio.sleep(0.3)
        yield StreamEvent("stage", stage)

    # Opening analysis lines — feels like the agent is reading the diff
    for line in review["opening"]:
        await asyncio.sleep(0.45)
        yield StreamEvent("output", line + "\n")

    # Per-finding output with a longer pause to simulate "thinking"
    for finding in review["findings"]:
        await asyncio.sleep(0.85)
        yield StreamEvent("output", finding + "\n")

    # Summary
    await asyncio.sleep(0.5)
    yield StreamEvent("output", review["summary"] + "\n")

    yield StreamEvent("exit", "0")


def sse_encode(event: StreamEvent) -> str:
    """Format a StreamEvent as a Server-Sent Events message."""
    return f"event: {event.kind}\ndata: {event.data}\n\n"


@app.get("/reviews/stream/{pr_id}")
async def stream_review(pr_id: str):
    if pr_id not in PULL_REQUESTS:
        raise HTTPException(status_code=404, detail=f"PR '{pr_id}' not found")

    async def generate():
        async for event in mock_review_stream(pr_id):
            yield sse_encode(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
