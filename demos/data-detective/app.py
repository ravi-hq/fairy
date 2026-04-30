"""
Data Detective — FastAPI application.

Routes:
  GET  /analysis/datasets              — list all datasets
  GET  /analysis/stream/{dataset_id}   — SSE: initial analysis stream
  POST /analysis/followup/{dataset_id} — SSE: follow-up question stream
  GET  /                               — serve the UI
"""

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from datasets import (
    DATASETS,
    INITIAL_FINDINGS,
    get_followup_response,
)

app = FastAPI(title="Data Detective", version="1.0.0")

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

import os

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Mock streaming helpers
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    kind: str   # "stage" | "output" | "exit"
    data: str


async def mock_analysis_stream(dataset_id: str) -> AsyncIterator[StreamEvent]:
    """Simulate an AoD agent starting up, then narrating findings."""
    for stage in ["create_sprite", "provision_setup", "runtime_start"]:
        await asyncio.sleep(0.3)
        yield StreamEvent("stage", stage)

    findings = INITIAL_FINDINGS.get(dataset_id, [])
    for line in findings:
        await asyncio.sleep(0.65)
        yield StreamEvent("output", line + "\n")

    yield StreamEvent("exit", "0")


async def mock_followup_stream(dataset_id: str, question: str) -> AsyncIterator[StreamEvent]:
    """Simulate a follow-up turn, matching to pre-scripted responses."""
    lines = get_followup_response(dataset_id, question)
    for line in lines:
        await asyncio.sleep(0.5)
        yield StreamEvent("output", line + "\n")
    yield StreamEvent("exit", "0")


def sse_format(event: StreamEvent) -> str:
    """Format a StreamEvent as an SSE message."""
    payload = json.dumps({"kind": event.kind, "data": event.data})
    return f"data: {payload}\n\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), "r") as f:
        return HTMLResponse(f.read())


@app.get("/analysis/datasets")
async def list_datasets():
    """Return metadata for all available datasets (no rows)."""
    result = []
    for ds in DATASETS.values():
        result.append({
            "id": ds["id"],
            "title": ds["title"],
            "description": ds["description"],
            "row_count": ds["row_count"],
            "columns": ds["columns"],
            "icon": ds["icon"],
            "preview": ds["rows"][:8],
        })
    return {"datasets": result}


@app.get("/analysis/stream/{dataset_id}")
async def stream_analysis(dataset_id: str):
    """SSE endpoint: stream the initial narrative analysis for a dataset."""
    if dataset_id not in DATASETS:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    async def event_generator():
        async for event in mock_analysis_stream(dataset_id):
            yield sse_format(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class FollowupRequest(BaseModel):
    question: str


@app.post("/analysis/followup/{dataset_id}")
async def stream_followup(dataset_id: str, body: FollowupRequest):
    """SSE endpoint: stream a follow-up answer for a given question."""
    if dataset_id not in DATASETS:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    async def event_generator():
        async for event in mock_followup_stream(dataset_id, question):
            yield sse_format(event)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
