"""
Forge — team agent maker / internal App Store demo.

Self-contained FastAPI app. No real Agent on Demand calls. The /test endpoint
spawns an asyncio task that drips a hardcoded `mock_test_run` sequence onto a
per-run subscriber queue; the /stream endpoint replays it as Server-Sent Events.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8090
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from scenarios import (
    AGENTS,
    AUDIT,
    DAILY_SPEND,
    OWNERS,
    TOOLS,
    add_agent,
    get_agent,
    get_audit_kpis,
    get_footer_stats,
    get_run_detail,
    get_run_history,
    get_test_run,
    list_agents_summary,
)

app = FastAPI(title="Forge", description="Team agent maker — Agent on Demand demo.")

_HERE = Path(__file__).parent
_STATIC = _HERE / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# --- In-memory state --------------------------------------------------------

_runs: dict[str, dict[str, Any]] = {}
_subscribers: dict[str, list[asyncio.Queue]] = {}
_forks: list[dict[str, Any]] = []  # in-memory only; cleared on restart


# --- Mock streaming task ----------------------------------------------------

async def _mock_test_run(run_id: str, agent_id: str) -> None:
    """Drip the agent's hardcoded test run onto every subscriber's queue."""
    sequence = get_test_run(agent_id) or []
    try:
        for delay, kind, text in sequence:
            await asyncio.sleep(delay)
            event = {"kind": kind, "text": text}
            _runs[run_id]["events"].append(event)
            for q in _subscribers.get(run_id, []):
                await q.put(event)
        _runs[run_id]["status"] = "completed"
    except asyncio.CancelledError:
        _runs[run_id]["status"] = "cancelled"
        raise
    finally:
        # Sentinel: tell subscribers the stream is done.
        for q in _subscribers.get(run_id, []):
            await q.put(None)


# --- API: library + detail --------------------------------------------------

@app.get("/api/agents")
async def api_list_agents() -> dict[str, Any]:
    return {"agents": list_agents_summary()}


@app.get("/api/agents/{agent_id}")
async def api_get_agent(agent_id: str) -> dict[str, Any]:
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    return agent


# --- API: test runs ---------------------------------------------------------

@app.post("/api/agents/{agent_id}/test")
async def api_start_test(agent_id: str) -> dict[str, str]:
    if agent_id not in AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    run_id = secrets.token_hex(6)
    _runs[run_id] = {"agent_id": agent_id, "status": "running", "events": []}
    _subscribers[run_id] = []
    asyncio.create_task(_mock_test_run(run_id, agent_id))
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/stream")
async def api_stream(run_id: str) -> EventSourceResponse:
    if run_id not in _runs:
        raise HTTPException(404, f"Unknown run: {run_id}")

    queue: asyncio.Queue = asyncio.Queue()
    # Replay anything already buffered so a slightly-late subscriber doesn't miss the opening stages.
    for event in _runs[run_id]["events"]:
        await queue.put(event)
    if _runs[run_id]["status"] in ("completed", "cancelled"):
        await queue.put(None)
    else:
        _subscribers[run_id].append(queue)

    async def event_gen():
        while True:
            event = await queue.get()
            if event is None:
                yield {"event": "done", "data": json.dumps({"run_id": run_id})}
                return
            yield {"event": event["kind"], "data": json.dumps(event)}

    return EventSourceResponse(event_gen())


# --- API: forks (no-op success) ---------------------------------------------

class ForkBody(BaseModel):
    name: str
    system_prompt: str
    tools: list[str]


@app.post("/api/agents/{agent_id}/fork")
async def api_fork(agent_id: str, body: ForkBody) -> dict[str, Any]:
    if agent_id not in AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    record = {
        "id": secrets.token_hex(4),
        "from": agent_id,
        "name": body.name,
        "system_prompt": body.system_prompt,
        "tools": body.tools,
    }
    _forks.append(record)
    return {"ok": True, "fork": record}


# --- API: create new agent (wizard publish) ---------------------------------

class CreateAgentBody(BaseModel):
    name: str
    description: str
    category: str
    system_prompt: str
    tools: list[str]
    done_when: str


def _slugify(name: str) -> str:
    out = []
    for ch in name.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "new-agent"


@app.post("/api/agents")
async def api_create_agent(body: CreateAgentBody) -> dict[str, Any]:
    base = _slugify(body.name)
    agent_id = base
    n = 2
    while agent_id in AGENTS:
        agent_id = f"{base}-{n}"
        n += 1
    record = {
        "id": agent_id,
        "name": body.name.strip() or agent_id,
        "description": body.description.strip(),
        "version": 1,
        "category": body.category or "Other",
        "created_at": "2026-04-29",
        "owner": OWNERS["jake"],
        "tools": body.tools,
        "system_prompt": body.system_prompt,
        "done_when": body.done_when,
        "stats": {"runs_per_week": 0, "rating": None, "rating_count": 0, "fork_count": 0},
        "version_history": [
            {"version": 1, "date": "2026-04-29", "note": "Published from wizard."},
        ],
        "forks": [],
        "recent_runs": [],
        # No mock_test_run — get_test_run() falls back to the generic 6-step sequence.
    }
    add_agent(record)
    return {k: v for k, v in record.items() if k != "mock_test_run"}


# --- API: run history -------------------------------------------------------

@app.get("/api/agents/{agent_id}/runs")
async def api_run_history(agent_id: str) -> dict[str, Any]:
    if agent_id not in AGENTS:
        raise HTTPException(404, f"Unknown agent: {agent_id}")
    return {"runs": get_run_history(agent_id)}


@app.get("/api/agents/{agent_id}/runs/{run_id}")
async def api_run_detail(agent_id: str, run_id: str) -> dict[str, Any]:
    detail = get_run_detail(agent_id, run_id)
    if detail is None:
        raise HTTPException(404, f"Unknown run: {run_id}")
    return detail


# --- API: audit -------------------------------------------------------------

@app.get("/api/audit")
async def api_audit() -> dict[str, Any]:
    return {**AUDIT, "kpis": get_audit_kpis(), "daily_spend": DAILY_SPEND}


# --- API: tools / footer ----------------------------------------------------

@app.get("/api/tools")
async def api_tools() -> dict[str, Any]:
    catalog = []
    for tool_name, info in TOOLS.items():
        used_by = [a["id"] for a in AGENTS.values() if tool_name in a["tools"]]
        catalog.append({**info, "used_by": used_by})
    return {"tools": catalog}


@app.get("/api/footer")
async def api_footer() -> dict[str, Any]:
    return get_footer_stats()


# --- Index ------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_STATIC / "index.html"))
