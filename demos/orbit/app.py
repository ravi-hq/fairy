"""
Orbit — Atlassian reimagined for an agent + human workforce.

FastAPI app:
- GET  /                              serves the single-page UI
- GET  /api/missions                  list of missions
- POST /api/missions                  create mission (Plan-a-mission wizard)
- GET  /api/missions/{id}             mission detail
- GET  /api/missions/{id}/stream      SSE stream of the running task line for
                                      Mission #1's regression suite (cycles)
- GET  /api/docs                      list of auto-generated KB docs
- GET  /api/docs/{id}                 single doc with full markdown body
- GET  /api/templates                 mission-template gallery
- GET  /api/templates/{key}           single template (for wizard step 2 prefill)
- POST /api/templates/match           keyword-match an outcome -> template key
- GET  /api/tasks/{task_id}/log       hardcoded session log for a task
- GET  /api/firehose                  firehose feed (all-mission activity)
- GET  /api/firehose/stream           SSE stream of new firehose events
- GET  /api/profiles/{name}           assignee profile (agent or human)
- GET  /api/workspace                 workspace-level metrics for top bar
"""

import asyncio
import itertools
import json
import time
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from scenarios import (
    AGENT_PROFILES,
    FIREHOSE_EVENTS,
    FIREHOSE_TICKER,
    KB_DOCS,
    MISSION_TEMPLATES,
    MISSIONS,
    REGRESSION_LINES,
    TASK_SESSION_LOGS,
    WORKSPACE_METRICS,
    ActivityEvent,
    Mission,
    Task,
    match_template,
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Orbit — agent-native Atlassian")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Track auto-progress tasks so we don't double-spawn on hot-reload.
_progress_tasks: dict = {}


def _task_to_dict(t: Task) -> dict:
    return asdict(t)


def _mission_to_dict(mission: Mission) -> dict:
    data = asdict(mission)
    data["progress"] = mission.progress
    data["progress_pct"] = mission.progress_pct
    data["spent_usd"] = round(sum(t.cost_usd for t in mission.tasks), 2)
    agent_min = sum(t.duration_min for t in mission.tasks if t.assignee_kind == "agent")
    human_min = sum(t.duration_min for t in mission.tasks if t.assignee_kind == "human")
    data["agent_minutes"] = agent_min
    data["human_minutes"] = human_min
    data["tasks_done"] = sum(1 for t in mission.tasks if t.status == "done")
    data["tasks_total"] = len(mission.tasks)
    return data


def _next_mission_id() -> int:
    return max((m.id for m in MISSIONS), default=0) + 1


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
            "spent_usd": round(sum(t.cost_usd for t in m.tasks), 2),
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


# --- Plan-a-mission wizard --------------------------------------------------

class TaskInput(BaseModel):
    title: str
    assignee: str
    assignee_kind: str  # "agent" | "human"


class CreateMissionRequest(BaseModel):
    title: str
    outcome: str
    owner: str
    target: str
    tasks: list[TaskInput]


async def _auto_progress(mission_id: int) -> None:
    """
    Simulate progress: every ~6s advance the next queued task to running,
    after 8s mark it done. Cap at 3 transitions then stop.

    We ALSO advance the corresponding mission activity feed so the UI
    reflects the change after a refetch.
    """
    transitions = 0
    while transitions < 3:
        await asyncio.sleep(6)
        m = next((mm for mm in MISSIONS if mm.id == mission_id), None)
        if m is None:
            return
        nxt = next((t for t in m.tasks if t.status == "queued"), None)
        if nxt is None:
            return
        nxt.status = "running"
        nxt.detail = "in progress…"
        m.activity.insert(0, ActivityEvent("just now", f"{nxt.assignee}: started '{nxt.title}'", kind="agent" if nxt.assignee_kind == "agent" else "human"))
        m.last_activity = "just now"
        await asyncio.sleep(8)
        nxt.status = "done"
        nxt.detail = None
        m.activity.insert(0, ActivityEvent("just now", f"{nxt.assignee}: completed '{nxt.title}'", kind="agent" if nxt.assignee_kind == "agent" else "human"))
        m.last_activity = "just now"
        transitions += 1


@app.post("/api/missions")
async def create_mission(req: CreateMissionRequest):
    new_id = _next_mission_id()
    initials = (req.owner[:2] or "??").upper()
    tasks: list[Task] = []
    for i, t in enumerate(req.tasks, start=1):
        # First task immediately running, rest queued.
        status = "running" if i == 1 else "queued"
        detail = "in progress…" if status == "running" else None
        tasks.append(Task(
            id=f"m{new_id}-t{i}",
            title=t.title,
            assignee=t.assignee,
            assignee_kind=t.assignee_kind,
            status=status,
            detail=detail,
            cost_usd=0.0,
            duration_min=0,
        ))
    mission = Mission(
        id=new_id,
        title=req.title,
        status="in-flight",
        owner=req.owner,
        owner_initials=initials,
        started="just now",
        target=req.target or "TBD",
        outcome=req.outcome,
        tasks=tasks,
        activity=[
            ActivityEvent("just now", f"{req.owner} launched mission via wizard", kind="human"),
            ActivityEvent("just now", f"{tasks[0].assignee}: started '{tasks[0].title}'", kind="agent" if tasks[0].assignee_kind == "agent" else "human"),
        ],
        last_activity="just now",
    )
    MISSIONS.append(mission)

    # Spawn auto-progress loop. Track so we don't leak.
    if mission.id not in _progress_tasks:
        _progress_tasks[mission.id] = asyncio.create_task(_auto_progress(mission.id))

    return _mission_to_dict(mission)


# --- Templates --------------------------------------------------------------

@app.get("/api/templates")
async def list_templates():
    out = []
    for key, tpl in MISSION_TEMPLATES.items():
        if key == "generic":
            continue
        out.append({
            "key": key,
            "name": tpl["name"],
            "description": tpl["description"],
            "icon": tpl["icon"],
            "used_count": tpl["used_count"],
            "task_count": len(tpl["tasks"]),
        })
    return out


@app.get("/api/templates/{key}")
async def get_template(key: str):
    tpl = MISSION_TEMPLATES.get(key)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl


class MatchRequest(BaseModel):
    outcome: str


@app.post("/api/templates/match")
async def match(req: MatchRequest):
    key = match_template(req.outcome)
    return {"key": key, "template": MISSION_TEMPLATES[key]}


# --- Session logs -----------------------------------------------------------

@app.get("/api/tasks/{task_id}/log")
async def get_task_log(task_id: str):
    """
    Return a session log for a task. Falls back to a synthesized log
    for tasks that don't have a hand-crafted one.
    """
    log = TASK_SESSION_LOGS.get(task_id)
    # Find the task to learn assignee / human-or-agent.
    task = None
    for m in MISSIONS:
        for t in m.tasks:
            if t.id == task_id:
                task = t
                break
        if task:
            break
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if log is None:
        if task.assignee_kind == "human":
            log = [
                f"[review] {task.assignee} opened task '{task.title}'",
                "[review] no agent transcript — this is a human-assigned task",
                "[review] use the review panel to approve or request changes",
            ]
        else:
            log = [
                f"[setup] (no detailed log available for {task_id})",
                f"[agent] {task.assignee} would run here",
                f"[result] task '{task.title}' status: {task.status}",
            ]
    return {
        "task_id": task_id,
        "task_title": task.title,
        "assignee": task.assignee,
        "assignee_kind": task.assignee_kind,
        "status": task.status,
        "duration_min": task.duration_min,
        "cost_usd": task.cost_usd,
        "tokens": int(task.duration_min * 1850),  # made-up but plausible
        "lines": log,
    }


# --- Firehose ---------------------------------------------------------------

@app.get("/api/firehose")
async def firehose():
    return FIREHOSE_EVENTS


@app.get("/api/firehose/stream")
async def firehose_stream():
    """SSE: emits a new firehose event every ~8s, cycling through the ticker."""
    async def event_generator() -> AsyncIterator[dict]:
        cycle = itertools.cycle(FIREHOSE_TICKER)
        while True:
            await asyncio.sleep(8)
            evt = dict(next(cycle))
            evt["timestamp"] = "just now"
            evt["_id"] = int(time.time() * 1000)
            yield {"data": json.dumps(evt)}

    return EventSourceResponse(event_generator())


# --- Profiles ---------------------------------------------------------------

@app.get("/api/profiles/{name}")
async def get_profile(name: str):
    prof = AGENT_PROFILES.get(name)
    if not prof:
        # Unknown — synthesize a reasonable fallback.
        if name.startswith("@"):
            return {
                "kind": "human",
                "name": name,
                "tagline": "Team member",
                "role": "—",
                "load": "—",
            }
        return {
            "kind": "agent",
            "name": name,
            "tagline": "Agent",
            "model": "—",
            "recent_runs": 0,
            "success_rate": "—",
            "forge_url": "#",
        }
    return prof


# --- Workspace metrics ------------------------------------------------------

@app.get("/api/workspace")
async def workspace():
    return WORKSPACE_METRICS


# --- KB ---------------------------------------------------------------------

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
