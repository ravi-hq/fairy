"""
The Board That Ships — FastAPI backend
Kanban board where moving a ticket to "In Progress" spawns a mock agent
session that streams realistic output and opens a fake PR.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field, asdict
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

TICKETS: dict[str, dict] = {
    "t1": {
        "id": "t1",
        "title": "Add rate limiting to /api/search",
        "description": "The search endpoint has no rate limiting and is being hammered by scrapers. Implement token-bucket rate limiting (100 req/min per IP). Use Redis if available, fall back to in-memory.",
        "status": "todo",
        "pr_url": None,
    },
    "t2": {
        "id": "t2",
        "title": "Fix N+1 query in user dashboard",
        "description": "The dashboard loads each user's team memberships in a separate query inside a loop. Replace with a single JOIN or prefetch_related call. Affects GET /dashboard for all users with >1 team.",
        "status": "todo",
        "pr_url": None,
    },
    "t3": {
        "id": "t3",
        "title": "Migrate auth tokens to httpOnly cookies",
        "description": "Auth tokens are currently stored in localStorage, making them vulnerable to XSS. Move them to httpOnly, Secure, SameSite=Lax cookies. Update the login, logout, and refresh endpoints accordingly.",
        "status": "todo",
        "pr_url": None,
    },
}

# Track active agent tasks so we can cancel them
_active_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Mock AoD client
# ---------------------------------------------------------------------------

@dataclass
class StreamEvent:
    type: str   # "stage", "output", "exit"
    data: str


async def mock_stream(ticket: dict) -> AsyncIterator[StreamEvent]:
    """Simulate a realistic AoD agent session with asyncio delays."""
    pr_number = random.randint(1200, 1999)

    # Provisioning stages
    for stage in ["create_sprite", "provision_setup", "mcp_config", "runtime_start"]:
        await asyncio.sleep(0.4)
        yield StreamEvent(type="stage", data=stage)

    # Realistic agent output lines
    lines = [
        f"Reading ticket: {ticket['title']}",
        "Cloning repository...",
        "Exploring codebase structure...",
        "Found relevant files: src/api/routes.py, tests/test_api.py",
        "Analysing existing patterns...",
        "Planning implementation...",
        "Writing implementation...",
        "Running linter... no issues found.",
        "Running test suite...",
        "All 47 tests passing.",
        "Committing changes...",
        "Pushing branch to origin...",
        "Opening pull request...",
        f"PR opened: https://github.com/acme/app/pull/{pr_number}",
    ]

    for line in lines:
        await asyncio.sleep(0.6)
        yield StreamEvent(type="output", data=line + "\n")

    yield StreamEvent(type="exit", data="0")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="The Board That Ships")

# Mount static files
import pathlib
STATIC_DIR = pathlib.Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(content=html)


@app.get("/api/tickets")
async def get_tickets():
    return list(TICKETS.values())


@app.post("/api/tickets/{ticket_id}/start")
async def start_ticket(ticket_id: str):
    """Move a ticket to in-progress. The SSE stream drives the agent."""
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] != "todo":
        raise HTTPException(status_code=409, detail="Ticket is not in todo state")
    ticket["status"] = "in-progress"
    return ticket


@app.get("/api/tickets/{ticket_id}/stream")
async def stream_ticket(ticket_id: str):
    """SSE endpoint — streams agent events for a ticket."""
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    async def event_generator():
        try:
            async for event in mock_stream(ticket):
                payload = json.dumps({"type": event.type, "data": event.data})
                yield {"event": event.type, "data": payload}

                # When we receive an exit event, update state
                if event.type == "exit":
                    break

                # Extract PR URL from output lines
                if event.type == "output" and "PR opened:" in event.data:
                    url = event.data.strip().split("PR opened:")[-1].strip()
                    ticket["pr_url"] = url
                    ticket["status"] = "in-review"
                    # Send a state-change event so the UI can react
                    state_payload = json.dumps({"ticket": ticket})
                    yield {"event": "state_change", "data": state_payload}
        except asyncio.CancelledError:
            pass

    return EventSourceResponse(event_generator())


@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    ticket = TICKETS.get(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket
