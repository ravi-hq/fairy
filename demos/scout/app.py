"""
Scout — Incident Response Co-pilot
FastAPI app: alert feed, SSE streaming investigation, structured brief.
"""

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from scenarios import SCENARIOS, SCENARIO_ORDER, Brief

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = FastAPI(title="Scout — Incident Response Co-pilot")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# Tracks which scenario fires next (cycles through the 3)
_next_scenario_index: int = 0

# Active investigations: investigation_id -> dict with status, events, brief
_investigations: Dict[str, dict] = {}

# SSE subscriber queues: investigation_id -> list of asyncio.Queue
_subscribers: Dict[str, list] = {}


# ---------------------------------------------------------------------------
# Mock AoD client
# ---------------------------------------------------------------------------

async def mock_investigate(scenario_key: str, investigation_id: str) -> None:
    """
    Simulates an AoD agent session: provisioning stages then investigation output.
    Pushes events into subscriber queues as they are produced.
    """
    scenario = SCENARIOS[scenario_key]

    async def _push(event_type: str, data: str) -> None:
        payload = json.dumps({"type": event_type, "data": data})
        _investigations[investigation_id]["events"].append(
            {"type": event_type, "data": data}
        )
        for q in _subscribers.get(investigation_id, []):
            await q.put(payload)

    # --- Provisioning stages ---
    for stage in ["create_sprite", "provision_setup", "runtime_start"]:
        await asyncio.sleep(0.3)
        await _push("stage", stage)

    # --- Investigation lines ---
    for line in scenario.investigation_steps:
        await asyncio.sleep(0.8)
        await _push("output", line)

    # --- Done: attach brief ---
    brief = scenario.brief
    _investigations[investigation_id]["brief"] = asdict(brief)
    _investigations[investigation_id]["status"] = "complete"
    await _push("brief", json.dumps(asdict(brief)))
    await _push("exit", "0")

    # Signal all subscribers to close
    for q in _subscribers.get(investigation_id, []):
        await q.put(None)  # sentinel


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("static/index.html") as f:
        return f.read()


@app.post("/api/fire-alert")
async def fire_alert():
    """Fire the next pre-loaded alert scenario and start an investigation."""
    global _next_scenario_index

    scenario_key = SCENARIO_ORDER[_next_scenario_index % len(SCENARIO_ORDER)]
    _next_scenario_index += 1

    scenario = SCENARIOS[scenario_key]
    investigation_id = str(uuid.uuid4())
    fired_at = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    _investigations[investigation_id] = {
        "id": investigation_id,
        "scenario_key": scenario_key,
        "alert": {
            "id": scenario.alert.id,
            "priority": scenario.alert.priority,
            "service": scenario.alert.service,
            "name": scenario.alert.name,
            "description": scenario.alert.description,
        },
        "fired_at": fired_at,
        "status": "investigating",
        "events": [],
        "brief": None,
    }
    _subscribers[investigation_id] = []

    # Run investigation in background
    asyncio.create_task(mock_investigate(scenario_key, investigation_id))

    return {
        "investigation_id": investigation_id,
        "alert": _investigations[investigation_id]["alert"],
        "fired_at": fired_at,
        "scenario_key": scenario_key,
    }


@app.get("/api/investigations")
async def list_investigations():
    """Return all investigations (most recent first)."""
    items = list(_investigations.values())
    items.reverse()
    return items


@app.get("/api/investigations/{investigation_id}")
async def get_investigation(investigation_id: str):
    inv = _investigations.get(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return inv


@app.get("/api/investigations/{investigation_id}/stream")
async def stream_investigation(investigation_id: str):
    """SSE endpoint — streams investigation events as they are produced."""
    if investigation_id not in _investigations:
        raise HTTPException(status_code=404, detail="Investigation not found")

    inv = _investigations[investigation_id]

    async def event_generator() -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()

        # Replay already-produced events so late subscribers catch up
        for evt in inv["events"]:
            payload = json.dumps({"type": evt["type"], "data": evt["data"]})
            yield {"data": payload}

        # If investigation is already complete, stop
        if inv["status"] == "complete":
            return

        # Subscribe for future events
        _subscribers[investigation_id].append(q)
        try:
            while True:
                item = await asyncio.wait_for(q.get(), timeout=120)
                if item is None:  # sentinel = done
                    break
                yield {"data": item}
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                _subscribers[investigation_id].remove(q)
            except ValueError:
                pass

    return EventSourceResponse(event_generator())
