"""
Brief — concurrent research fleet demo
FastAPI app with multiplexed SSE streaming for 4 concurrent agent sessions.
"""

import asyncio
import json
import random
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from research_data import (
    DIMENSIONS,
    DIMENSION_LABELS,
    RESEARCH_DATA,
    SYNTHESIS_DATA,
)

app = FastAPI(title="Brief — Research Fleet Demo")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ---------------------------------------------------------------------------
# Mock AoD client
# ---------------------------------------------------------------------------

async def mock_research_stream(target: str, dimension: str) -> AsyncIterator[dict]:
    """
    Simulate a concurrent agent session for one research dimension.
    Yields events with a 'session' field so the multiplexer can route them.
    """
    # Startup stages — feel like real session lifecycle
    for stage in ["create_sprite", "runtime_start"]:
        await asyncio.sleep(0.2 + random.random() * 0.3)
        yield {"session": dimension, "type": "stage", "data": stage}

    # Stream research lines at variable speed
    lines = RESEARCH_DATA.get(target, {}).get(dimension, [
        f"Researching {dimension} for {target}...",
        "No pre-loaded data found for this target.",
        "In a real deployment, an agent would query live sources here.",
    ])

    for line in lines:
        await asyncio.sleep(0.4 + random.random() * 0.6)
        yield {"session": dimension, "type": "output", "data": line}

    yield {"session": dimension, "type": "exit", "data": "0"}


async def mock_synthesis_stream(target: str) -> AsyncIterator[dict]:
    """
    Simulate the synthesis agent that assembles the final brief.
    Runs after all 4 dimension agents complete.
    """
    synth = SYNTHESIS_DATA.get(target)
    if not synth:
        yield {"type": "synthesis_line", "data": f"Synthesizing research on {target}..."}
        await asyncio.sleep(1.0)
        yield {"type": "synthesis_line", "data": "No pre-loaded synthesis data. In production, an agent would generate this."}
        yield {"type": "synthesis_done", "data": ""}
        return

    yield {"type": "synthesis_start", "data": ""}
    await asyncio.sleep(0.5)

    # Stream the summary
    yield {"type": "synthesis_section", "data": "summary"}
    await asyncio.sleep(0.3)
    yield {"type": "synthesis_line", "data": synth["summary"]}
    await asyncio.sleep(0.8)

    # Key themes
    yield {"type": "synthesis_section", "data": "key_themes"}
    for theme in synth["key_themes"]:
        await asyncio.sleep(0.4 + random.random() * 0.3)
        yield {"type": "synthesis_line", "data": theme}

    await asyncio.sleep(0.5)

    # Risks
    yield {"type": "synthesis_section", "data": "risks"}
    for risk in synth["risks"]:
        await asyncio.sleep(0.4 + random.random() * 0.3)
        yield {"type": "synthesis_line", "data": risk}

    await asyncio.sleep(0.5)

    # Opportunities
    yield {"type": "synthesis_section", "data": "opportunities"}
    for opp in synth["opportunities"]:
        await asyncio.sleep(0.4 + random.random() * 0.3)
        yield {"type": "synthesis_line", "data": opp}

    await asyncio.sleep(0.5)

    # Open questions
    yield {"type": "synthesis_section", "data": "open_questions"}
    for q in synth["open_questions"]:
        await asyncio.sleep(0.4 + random.random() * 0.3)
        yield {"type": "synthesis_line", "data": q}

    await asyncio.sleep(0.3)
    yield {"type": "synthesis_done", "data": ""}


# ---------------------------------------------------------------------------
# Multiplexed SSE endpoint
# ---------------------------------------------------------------------------

async def run_research_fleet(target: str, queue: asyncio.Queue):
    """
    Fan out 4 concurrent research sessions, feeding all events into a shared queue.
    When all 4 are done, run the synthesis agent.
    """
    async def pipe_dimension(dimension: str):
        async for event in mock_research_stream(target, dimension):
            await queue.put(event)

    # Run all 4 dimensions concurrently
    await asyncio.gather(*[pipe_dimension(dim) for dim in DIMENSIONS])

    # Signal that research phase is complete
    await queue.put({"type": "research_complete", "data": ""})

    # Run synthesis
    async for event in mock_synthesis_stream(target):
        await queue.put(event)

    # Signal stream end
    await queue.put(None)


@app.get("/research/stream")
async def research_stream(request: Request, target: str):
    """
    Single multiplexed SSE endpoint. Runs 4 concurrent research agents
    and one synthesis agent, streaming all events to the client.
    Each event has a 'session' field for client-side routing.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        # Kick off the research fleet in the background
        fleet_task = asyncio.create_task(run_research_fleet(target, queue))

        try:
            while True:
                # Respect client disconnects
                if await request.is_disconnected():
                    fleet_task.cancel()
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Send a keepalive comment
                    yield {"comment": "keepalive"}
                    continue

                if event is None:
                    # End of stream
                    break

                yield {"data": json.dumps(event)}

        except asyncio.CancelledError:
            fleet_task.cancel()

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

@app.get("/targets")
async def list_targets():
    """Return the list of pre-loaded research targets."""
    return {
        "targets": list(RESEARCH_DATA.keys()),
        "dimensions": DIMENSION_LABELS,
    }


# ---------------------------------------------------------------------------
# Serve the frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html") as f:
        return f.read()
