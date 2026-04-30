"""
Relay — Slack reimagined where agents are first-class teammates.

This is a self-contained POC: in-memory state, mock scenarios, no real
Agent on Demand or LLM calls. Run with:

    uvicorn demos.relay.app:app --reload --port 8910

The frontend opens a single SSE EventSource on /api/stream and renders
patches as they arrive.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

try:
    from .scenarios import SCENES
except ImportError:
    # Allow running as `uvicorn app:app` from inside demos/relay/.
    from scenarios import SCENES

# ---------------------------------------------------------------------------
# Static workspace data — humans, agents, channels.
# ---------------------------------------------------------------------------

MEMBERS: dict[str, dict[str, Any]] = {
    # Humans.
    "jake": {
        "id": "jake",
        "kind": "human",
        "name": "Jake G",
        "title": "founder",
        "initials": "JG",
        "color": "#bc8cff",
    },
    "maya": {
        "id": "maya",
        "kind": "human",
        "name": "Maya M",
        "title": "staff engineer",
        "initials": "MM",
        "color": "#79c0ff",
    },
    "sam": {
        "id": "sam",
        "kind": "human",
        "name": "Sam P",
        "title": "head of product",
        "initials": "SP",
        "color": "#ffa657",
    },
    # Agents.
    "scout": {
        "id": "scout",
        "kind": "agent",
        "name": "Scout",
        "role": "on-call investigations",
        "presence": "idle",
        "current_activity": "ready",
        "channels": ["on-call", "engineering"],
    },
    "researcher": {
        "id": "researcher",
        "kind": "agent",
        "name": "Researcher",
        "role": "market & competitive intel",
        "presence": "idle",
        "current_activity": "ready",
        "channels": ["competitive-intel", "growth", "general"],
    },
    "migrator": {
        "id": "migrator",
        "kind": "agent",
        "name": "Migrator",
        "role": "database operations",
        "presence": "idle",
        "current_activity": "ready",
        "channels": ["engineering"],
    },
    "pr-bot": {
        "id": "pr-bot",
        "kind": "agent",
        "name": "PR Bot",
        "role": "pull request review summaries",
        "presence": "idle",
        "current_activity": "ready",
        "channels": ["engineering", "general"],
    },
    # System bots — render as agents but aren't AoD-managed.
    "bot-pagerduty": {
        "id": "bot-pagerduty",
        "kind": "system",
        "name": "PagerDuty",
        "role": "alert relay",
        "channels": ["on-call"],
    },
}

CHANNELS: dict[str, dict[str, Any]] = {
    "general": {
        "id": "general",
        "name": "general",
        "topic": "company-wide announcements",
        "members": ["jake", "maya", "sam", "researcher", "pr-bot"],
    },
    "engineering": {
        "id": "engineering",
        "name": "engineering",
        "topic": "ship logs, design docs, deploys",
        "members": ["jake", "maya", "scout", "migrator", "pr-bot"],
    },
    "on-call": {
        "id": "on-call",
        "name": "on-call",
        "topic": "production alerts and incident response",
        "members": ["jake", "maya", "scout", "bot-pagerduty"],
    },
    "competitive-intel": {
        "id": "competitive-intel",
        "name": "competitive-intel",
        "topic": "what others are shipping and why we should care",
        "members": ["jake", "sam", "researcher"],
    },
    "growth": {
        "id": "growth",
        "name": "growth",
        "topic": "acquisition, activation, retention",
        "members": ["jake", "sam", "researcher"],
    },
}

DM_THREADS: list[dict[str, Any]] = [
    {"id": "dm-maya", "with": ["maya"], "label": "Maya M"},
    {"id": "dm-sam", "with": ["sam"], "label": "Sam P"},
    {"id": "dm-scout", "with": ["scout"], "label": "Scout"},
]

# ---------------------------------------------------------------------------
# Initial timeline content — gives every channel a cold-start feel.
# ---------------------------------------------------------------------------

SEED_MESSAGES: dict[str, list[dict[str, Any]]] = {
    "general": [
        {
            "id": "seed-general-1",
            "author_id": "sam",
            "kind": "user",
            "text": "Reminder: Q2 planning doc is due Friday. Drop comments by EOD Wed.",
            "ts_offset_min": -180,
        },
        {
            "id": "seed-general-2",
            "author_id": "researcher",
            "kind": "user",
            "text": (
                "Weekly competitive scan went out — three things worth a "
                "look in #competitive-intel."
            ),
            "ts_offset_min": -90,
        },
    ],
    "engineering": [
        {
            "id": "seed-eng-1",
            "author_id": "maya",
            "kind": "user",
            "text": (
                "Heads up — landing the checkout-service v2.4.1 rollout in ~10. "
                "PR #4821, two approvals."
            ),
            "ts_offset_min": -45,
        },
        {
            "id": "seed-eng-2",
            "author_id": "pr-bot",
            "kind": "user",
            "text": (
                "PR #4821 summary: removes per-request DB connection reuse in "
                "CheckoutSession.complete. Net -34/+12 lines. Two approvals "
                "from @scout (auto) and @maya."
            ),
            "ts_offset_min": -42,
        },
        {
            "id": "seed-eng-3",
            "author_id": "maya",
            "kind": "user",
            "text": "deploy out, all green",
            "ts_offset_min": -16,
        },
    ],
    "on-call": [
        {
            "id": "seed-oncall-1",
            "author_id": "scout",
            "kind": "user",
            "text": (
                "Last 24h: 0 SEV-1, 1 SEV-3 (resolved). pgbouncer pool "
                "utilization on checkout-service trending up — keeping an eye."
            ),
            "ts_offset_min": -240,
        },
    ],
    "competitive-intel": [
        {
            "id": "seed-ci-1",
            "author_id": "sam",
            "kind": "user",
            "text": (
                "Linear shipped 'Agents for Linear' last week. Researcher "
                "did a writeup — short version: not a threat yet, very "
                "Linear-shaped, no presence model."
            ),
            "ts_offset_min": -1440,
        },
    ],
    "growth": [
        {
            "id": "seed-growth-1",
            "author_id": "sam",
            "kind": "user",
            "text": (
                "Activation funnel from last week: 41% → 38% drop on the "
                "first-agent-spawn step. Researcher, can you dig?"
            ),
            "ts_offset_min": -360,
        },
        {
            "id": "seed-growth-2",
            "author_id": "researcher",
            "kind": "user",
            "text": (
                "On it. First read: looks correlated with the new env-var "
                "step we added Tuesday. Will follow up by Thu."
            ),
            "ts_offset_min": -354,
        },
    ],
}


# ---------------------------------------------------------------------------
# Live workspace state — seeded fresh on import.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _seed_messages() -> dict[str, list[dict[str, Any]]]:
    """Stamp seed messages with absolute timestamps + empty thread lists."""
    out: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(tz=timezone.utc).timestamp()
    for channel_id, seeds in SEED_MESSAGES.items():
        out[channel_id] = []
        for seed in seeds:
            msg = dict(seed)
            offset = msg.pop("ts_offset_min", 0)
            msg["ts"] = datetime.fromtimestamp(
                now + offset * 60, tz=timezone.utc
            ).isoformat()
            msg["thread"] = []
            out[channel_id].append(msg)
    # Channels with no seeds still need an empty list.
    for channel_id in CHANNELS:
        out.setdefault(channel_id, [])
    return out


messages: dict[str, list[dict[str, Any]]] = _seed_messages()
active_scenes: set[str] = set()

# Subscribers receive each broadcast event. Each subscriber owns an asyncio
# Queue; the SSE generator drains it.
subscribers: list[asyncio.Queue[dict[str, Any]]] = []


async def broadcast(event: dict[str, Any]) -> None:
    """Fan out a single event to every connected SSE client."""
    dead: list[asyncio.Queue[dict[str, Any]]] = []
    for queue in subscribers:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(queue)
    for queue in dead:
        if queue in subscribers:
            subscribers.remove(queue)


# ---------------------------------------------------------------------------
# Mutators — apply an event to in-memory state, then broadcast it.
# ---------------------------------------------------------------------------

def _find_message(channel: str, message_id: str) -> dict[str, Any] | None:
    for msg in messages.get(channel, []):
        if msg["id"] == message_id:
            return msg
        for reply in msg.get("thread", []):
            if reply["id"] == message_id:
                return reply
    return None


async def apply_message(channel: str, message: dict[str, Any]) -> None:
    msg = dict(message)
    msg.setdefault("ts", _now_iso())
    msg.setdefault("thread", [])
    messages.setdefault(channel, []).append(msg)
    await broadcast({"type": "message", "channel": channel, "message": msg})


async def apply_message_update(
    channel: str, message_id: str, patch: dict[str, Any]
) -> None:
    msg = _find_message(channel, message_id)
    if msg is None:
        return
    msg.update(patch)
    await broadcast(
        {
            "type": "message_update",
            "channel": channel,
            "message_id": message_id,
            "patch": patch,
        }
    )


async def apply_thread_reply(
    channel: str, parent_id: str, message: dict[str, Any]
) -> None:
    parent = _find_message(channel, parent_id)
    if parent is None:
        return
    reply = dict(message)
    reply.setdefault("ts", _now_iso())
    parent.setdefault("thread", []).append(reply)
    await broadcast(
        {
            "type": "thread_reply",
            "channel": channel,
            "parent_id": parent_id,
            "message": reply,
        }
    )


async def apply_presence(
    agent_id: str, status: str, activity: str
) -> None:
    member = MEMBERS.get(agent_id)
    if member is None:
        return
    member["presence"] = status
    member["current_activity"] = activity
    await broadcast(
        {
            "type": "presence",
            "agent_id": agent_id,
            "status": status,
            "activity": activity,
        }
    )


async def apply_typing(channel: str, agent_id: str, on: bool) -> None:
    await broadcast(
        {
            "type": "typing",
            "channel": channel,
            "agent_id": agent_id,
            "on": on,
        }
    )


# ---------------------------------------------------------------------------
# Scene runner.
# ---------------------------------------------------------------------------

DISPATCH = {
    "message": lambda payload: apply_message(payload["channel"], payload["message"]),
    "message_update": lambda payload: apply_message_update(
        payload["channel"], payload["message_id"], payload["patch"]
    ),
    "thread_reply": lambda payload: apply_thread_reply(
        payload["channel"], payload["parent_id"], payload["message"]
    ),
    "presence": lambda payload: apply_presence(
        payload["agent_id"], payload["status"], payload["activity"]
    ),
    "typing": lambda payload: apply_typing(
        payload["channel"], payload["agent_id"], payload["on"]
    ),
}


async def _run_scene(scene_id: str) -> None:
    scene = SCENES.get(scene_id)
    if scene is None:
        return
    active_scenes.add(scene_id)
    try:
        prev_delay = 0.0
        for delay, kind, payload in scene["events"]:
            await asyncio.sleep(max(0.0, delay - prev_delay))
            prev_delay = delay
            handler = DISPATCH.get(kind)
            if handler is not None:
                await handler(payload)
    finally:
        active_scenes.discard(scene_id)


# ---------------------------------------------------------------------------
# FastAPI app.
# ---------------------------------------------------------------------------

app = FastAPI(title="Relay — agent-native chat")

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state() -> JSONResponse:
    """Initial workspace snapshot. The frontend hits this once on load."""
    return JSONResponse(
        {
            "workspace": {"name": "Acme Eng", "self_id": "jake"},
            "channels": list(CHANNELS.values()),
            "members": list(MEMBERS.values()),
            "dms": DM_THREADS,
            "messages": messages,
            "scenes": [
                {"id": s["id"], "label": s["label"], "channel": s["channel"]}
                for s in SCENES.values()
            ],
        }
    )


@app.post("/api/scenes/{scene_id}/run")
async def run_scene(scene_id: str) -> JSONResponse:
    if scene_id not in SCENES:
        raise HTTPException(status_code=404, detail="unknown scene")
    if scene_id in active_scenes:
        return JSONResponse(
            {"scene_id": scene_id, "status": "already_running"}
        )
    asyncio.create_task(_run_scene(scene_id))
    return JSONResponse({"scene_id": scene_id, "status": "started"})


@app.get("/api/stream")
async def stream() -> EventSourceResponse:
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    subscribers.append(queue)

    async def gen() -> AsyncIterator[dict[str, Any]]:
        try:
            # Send a hello so the client can confirm the stream is open.
            yield {"event": "hello", "data": json.dumps({"ok": True})}
            while True:
                event = await queue.get()
                yield {
                    "event": event.get("type", "message"),
                    "data": json.dumps(event),
                }
        finally:
            if queue in subscribers:
                subscribers.remove(queue)

    return EventSourceResponse(gen())
