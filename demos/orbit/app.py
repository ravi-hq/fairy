"""
Orbit — Atlassian reimagined for an agent + human workforce.

FastAPI app:
- GET /                              serves the single-page UI
- GET /api/missions                  list of missions
- GET /api/missions/{id}             mission detail
- GET /api/missions/{id}/stream      SSE stream of the running task line for
                                     Mission #1's regression suite (cycles)
- GET /api/docs                      list of auto-generated KB docs
- GET /api/docs/{id}                 single doc with full markdown body
"""

import asyncio
import json
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from scenarios import KB_DOCS, MISSIONS, REGRESSION_LINES

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Orbit — agent-native Atlassian")
app.mount("/static", StaticFiles(directory="static"), name="static")


def _mission_to_dict(mission) -> dict:
    data = asdict(mission)
    data["progress"] = mission.progress
    data["progress_pct"] = mission.progress_pct
    return data


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html") as f:
        return f.read()


@app.get("/api/missions")
async def list_missions():
    """List missions for the left rail."""
    return [
        {
            "id": m.id,
            "title": m.title,
            "status": m.status,
            "owner": m.owner,
            "owner_initials": m.owner_initials,
            "progress": m.progress,
            "progress_pct": m.progress_pct,
            "last_activity": m.last_activity,
        }
        for m in MISSIONS
    ]


@app.get("/api/missions/{mission_id}")
async def get_mission(mission_id: int):
    for m in MISSIONS:
        if m.id == mission_id:
            return _mission_to_dict(m)
    raise HTTPException(status_code=404, detail="Mission not found")


@app.get("/api/missions/{mission_id}/stream")
async def stream_mission(mission_id: int):
    """
    SSE stream for the Scout regression-suite live line.

    Only Mission #1 has a running agent task. For other missions we close the
    stream immediately. Lines cycle through REGRESSION_LINES so the demo
    always shows motion.
    """
    if not any(m.id == mission_id for m in MISSIONS):
        raise HTTPException(status_code=404, detail="Mission not found")

    async def event_generator() -> AsyncIterator[dict]:
        if mission_id != 1:
            yield {"data": json.dumps({"type": "idle"})}
            return

        i = 0
        while True:
            line = REGRESSION_LINES[i % len(REGRESSION_LINES)]
            yield {
                "data": json.dumps(
                    {"type": "line", "task_id": "m1-t5", "text": line}
                )
            }
            i += 1
            await asyncio.sleep(1.5)

    return EventSourceResponse(event_generator())


@app.get("/api/docs")
async def list_docs():
    return [
        {
            "id": d.id,
            "title": d.title,
            "category": d.category,
            "source_mission_id": d.source_mission_id,
            "source_mission_title": d.source_mission_title,
            "generated_at": d.generated_at,
            "event_count": d.event_count,
        }
        for d in KB_DOCS
    ]


@app.get("/api/docs/{doc_id}")
async def get_doc(doc_id: int):
    for d in KB_DOCS:
        if d.id == doc_id:
            return asdict(d)
    raise HTTPException(status_code=404, detail="Doc not found")
