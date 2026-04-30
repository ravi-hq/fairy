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
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

try:
    from .scenarios import (
        AGENT_PROFILES,
        AGENT_RESPONDERS,
        AMBIENT_ACTIVITY,
        HUMAN_PROFILES,
        INITIAL_UNREADS,
        SCENES,
        SEED_DMS,
        SEED_HISTORY,
    )
except ImportError:
    # Allow running as `uvicorn app:app` from inside demos/relay/.
    from scenarios import (  # type: ignore[no-redef]
        AGENT_PROFILES,
        AGENT_RESPONDERS,
        AMBIENT_ACTIVITY,
        HUMAN_PROFILES,
        INITIAL_UNREADS,
        SCENES,
        SEED_DMS,
        SEED_HISTORY,
    )

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

# DM threads — id corresponds to a "channel" key in `messages` so the same
# rendering pipeline works for channels and DMs.
DM_THREADS: list[dict[str, Any]] = [
    {"id": "dm-scout", "with": ["scout"], "label": "Scout"},
    {"id": "dm-researcher", "with": ["researcher"], "label": "Researcher"},
    {"id": "dm-maya", "with": ["maya"], "label": "Maya M"},
    {"id": "dm-sam", "with": ["sam"], "label": "Sam P"},
]

# Mention shortcuts (the user types `@prbot`, we resolve to `pr-bot`).
MENTION_ALIASES: dict[str, str] = {
    "scout": "scout",
    "researcher": "researcher",
    "research": "researcher",
    "migrator": "migrator",
    "prbot": "pr-bot",
    "pr-bot": "pr-bot",
    "pr_bot": "pr-bot",
}

AGENT_IDS: set[str] = {mid for mid, m in MEMBERS.items() if m.get("kind") == "agent"}
DM_IDS: set[str] = {dm["id"] for dm in DM_THREADS}


# ---------------------------------------------------------------------------
# Live workspace state — seeded fresh on import.
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _stamp(offset_min: float) -> str:
    base = datetime.now(tz=timezone.utc).timestamp()
    return datetime.fromtimestamp(base + offset_min * 60, tz=timezone.utc).isoformat()


def _seed_messages() -> dict[str, list[dict[str, Any]]]:
    """Stamp seed messages with absolute timestamps + empty thread lists."""
    out: dict[str, list[dict[str, Any]]] = {}
    for channel_id, seeds in SEED_HISTORY.items():
        out[channel_id] = []
        for i, seed in enumerate(seeds):
            msg = dict(seed)
            offset = msg.pop("ts_offset_min", 0)
            msg.setdefault("id", f"seed-{channel_id}-{i}")
            msg.setdefault("kind", "user")
            msg["ts"] = _stamp(offset)
            msg["thread"] = []
            out[channel_id].append(msg)
    for channel_id in CHANNELS:
        out.setdefault(channel_id, [])

    # DMs share the same message store, keyed by their dm id.
    for dm_id, dm in SEED_DMS.items():
        out[dm_id] = []
        for i, seed in enumerate(dm.get("messages", [])):
            msg = dict(seed)
            offset = msg.pop("ts_offset_min", 0)
            msg.setdefault("id", f"seed-{dm_id}-{i}")
            msg.setdefault("kind", "user")
            msg["ts"] = _stamp(offset)
            msg["thread"] = []
            out[dm_id].append(msg)
    return out


messages: dict[str, list[dict[str, Any]]] = _seed_messages()
active_scenes: set[str] = set()

# Subscribers receive each broadcast event.
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
    msg.setdefault("id", _new_id("m"))
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


def _new_id(prefix: str = "m") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Agent responder engine.
#
# Picks a responder template from AGENT_RESPONDERS based on the prompt text,
# then plays it out as: presence → empty stream message → step updates →
# final reply (text or structured card) → presence back to idle.
# ---------------------------------------------------------------------------

def _pick_template(agent_id: str, prompt: str) -> dict[str, Any] | None:
    templates = AGENT_RESPONDERS.get(agent_id)
    if not templates:
        return None
    lc = prompt.lower()
    fallback = None
    for tpl in templates:
        kws = tpl.get("keywords") or []
        if not kws:
            fallback = tpl
            continue
        for kw in kws:
            if kw.lower() in lc:
                return tpl
    return fallback


async def _run_agent_response(
    channel: str, agent_id: str, prompt: str, in_thread_of: str | None = None
) -> None:
    """Drive a single agent's response to a user prompt or slash command.

    If `in_thread_of` is provided, the streaming message + final reply are
    posted as thread replies on that parent. Otherwise they're channel
    messages.
    """
    tpl = _pick_template(agent_id, prompt)
    if tpl is None:
        return

    activity = tpl.get("activity", "working")
    steps = tpl.get("steps") or []
    reply = tpl.get("reply")

    # Presence + typing.
    await apply_presence(agent_id, "working", activity)
    await apply_typing(channel, agent_id, True)
    await asyncio.sleep(0.5)

    # Empty stream message.
    stream_id = _new_id("stream")
    stream_msg = {
        "id": stream_id,
        "author_id": agent_id,
        "kind": "agent_stream",
        "text": "On it.",
        "steps": [],
        "streaming": True,
    }
    if in_thread_of:
        await apply_thread_reply(channel, in_thread_of, stream_msg)
    else:
        await apply_message(channel, stream_msg)

    # Build steps progressively. Slight per-step jitter feels alive.
    accumulated: list[dict[str, Any]] = []
    per_step = 1.1
    for step in steps:
        await asyncio.sleep(per_step)
        accumulated.append({**step, "state": "done"})
        await apply_message_update(channel, stream_id, {"steps": list(accumulated)})

    # Wrap the stream.
    await asyncio.sleep(0.6)
    await apply_message_update(
        channel,
        stream_id,
        {"text": "Done.", "streaming": False, "steps": list(accumulated)},
    )
    await apply_typing(channel, agent_id, False)

    # Post the final reply. If `reply` is a string, it's a plain text message
    # in the same channel/thread. If it's a dict, treat it as a full message
    # payload (with `kind`, structured card field, etc.).
    if reply is not None:
        if isinstance(reply, str):
            final = {
                "id": _new_id("m"),
                "author_id": agent_id,
                "kind": "user",
                "text": reply,
            }
        else:
            final = {"id": _new_id("m"), "author_id": agent_id, **reply}
        if in_thread_of:
            await apply_thread_reply(channel, in_thread_of, final)
        else:
            await apply_message(channel, final)

    # Presence back to idle.
    await asyncio.sleep(0.2)
    await apply_presence(agent_id, "idle", "ready")


# ---------------------------------------------------------------------------
# Mention parsing + dispatch.
# ---------------------------------------------------------------------------

MENTION_RE = re.compile(r"@([a-z][a-z0-9_-]*)", re.IGNORECASE)


def _resolve_mentions(text: str) -> list[str]:
    """Return a list of agent_ids mentioned in `text`."""
    out: list[str] = []
    for m in MENTION_RE.finditer(text):
        token = m.group(1).lower()
        agent_id = MENTION_ALIASES.get(token)
        if agent_id and agent_id in AGENT_IDS and agent_id not in out:
            out.append(agent_id)
    return out


async def _post_system_message(channel: str, text: str) -> None:
    """Ephemeral-style system message — visible in the channel, not threaded."""
    await apply_message(
        channel,
        {
            "id": _new_id("sys"),
            "author_id": "bot-pagerduty",  # reuse the system avatar
            "kind": "system",
            "text": text,
            "system": True,
        },
    )


# ---------------------------------------------------------------------------
# Slash commands.
# ---------------------------------------------------------------------------

async def _handle_slash(channel: str, raw: str) -> dict[str, Any]:
    """Dispatch a `/command ...` and return a small ack dict."""
    body = raw[1:].strip()
    parts = body.split(maxsplit=1)
    if not parts:
        return {"ok": False, "error": "empty command"}
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if cmd == "help":
        await _post_system_message(
            channel,
            "**Slash commands**\n"
            "• `/scout investigate <query>` — Scout investigates and posts a brief\n"
            "• `/researcher brief <topic>` — Researcher posts a brief on a topic\n"
            "• `/migrator preflight <table>` — Migrator runs a pre-flight scan\n"
            "• `/poll <question> | <opt1> | <opt2> | ...` — post a poll\n"
            "• `/dm <agent>` — open a DM with an agent\n"
            "• `/help` — this list",
        )
        return {"ok": True, "kind": "help"}

    if cmd in ("scout", "researcher", "migrator", "prbot", "pr-bot"):
        agent_id = MENTION_ALIASES.get(cmd, cmd)
        # `/scout investigate <q>` — first word after the agent is a verb we
        # discard for matching, but keep in the prompt sent to the responder.
        prompt = rest if rest else cmd
        # Echo a user-side line so the channel shows the dispatch.
        author = MEMBERS["jake"]
        await apply_message(
            channel,
            {
                "id": _new_id("m"),
                "author_id": author["id"],
                "kind": "user",
                "text": f"/{cmd} {rest}".rstrip(),
            },
        )
        asyncio.create_task(_run_agent_response(channel, agent_id, prompt))
        return {"ok": True, "kind": "dispatch", "agent_id": agent_id}

    if cmd == "poll":
        # Format: question | opt1 | opt2 | ...
        bits = [b.strip() for b in rest.split("|") if b.strip()]
        if len(bits) < 3:
            await _post_system_message(
                channel,
                "Usage: `/poll question | option 1 | option 2 | ...` (need at least 2 options)",
            )
            return {"ok": False, "error": "poll needs at least 2 options"}
        question, options = bits[0], bits[1:]
        await apply_message(
            channel,
            {
                "id": _new_id("poll"),
                "author_id": "jake",
                "kind": "poll",
                "text": question,
                "poll": {
                    "question": question,
                    "options": [{"label": o, "votes": 0} for o in options],
                },
            },
        )
        return {"ok": True, "kind": "poll"}

    if cmd == "dm":
        target = rest.strip().lstrip("@").lower()
        agent_id = MENTION_ALIASES.get(target, target)
        if agent_id not in AGENT_IDS and agent_id not in {"maya", "sam"}:
            await _post_system_message(
                channel,
                f"`/dm {target}` — no such teammate. Try `scout`, `researcher`, `migrator`, `prbot`, `maya`, or `sam`.",
            )
            return {"ok": False, "error": "unknown user"}
        dm_id = f"dm-{agent_id}"
        if dm_id not in DM_IDS:
            # Best-effort: if no pre-seeded DM, the frontend will treat the
            # id as a known channel and start fresh.
            messages.setdefault(dm_id, [])
        return {"ok": True, "kind": "dm", "dm_id": dm_id}

    await _post_system_message(channel, f"Unknown command: `/{cmd}`. Try `/help`.")
    return {"ok": False, "error": f"unknown command: {cmd}"}


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


class PostMessageBody(BaseModel):
    text: str


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
            "ambient": AMBIENT_ACTIVITY,
            "unreads": INITIAL_UNREADS,
            "mention_aliases": MENTION_ALIASES,
        }
    )


@app.get("/api/profiles/{member_id}")
async def get_profile(member_id: str) -> JSONResponse:
    if member_id in AGENT_PROFILES:
        prof = dict(AGENT_PROFILES[member_id])
        # Stamp recent_activity timestamps.
        for item in prof.get("recent_activity", []):
            offset = item.pop("ts_offset_min", 0) if "ts_offset_min" in item else 0
            item["ts"] = _stamp(offset)
        return JSONResponse(prof)
    if member_id in HUMAN_PROFILES:
        return JSONResponse(HUMAN_PROFILES[member_id])
    raise HTTPException(status_code=404, detail="unknown member")


@app.post("/api/channels/{channel}/messages")
async def post_message(channel: str, body: PostMessageBody) -> JSONResponse:
    """Post a human-authored message into a channel or DM.

    If the message starts with `/`, it's a slash command. If it @-mentions
    an agent, that agent will respond. In an agent-DM, every message gets
    a response from the agent.
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty message")

    is_dm = channel.startswith("dm-")
    is_known = channel in CHANNELS or channel in DM_IDS or channel in messages
    if not is_known:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}")

    # Slash commands short-circuit.
    if text.startswith("/"):
        result = await _handle_slash(channel, text)
        return JSONResponse({"ok": result.get("ok", True), "result": result})

    # Post the human message.
    msg = {
        "id": _new_id("m"),
        "author_id": "jake",
        "kind": "user",
        "text": text,
    }
    await apply_message(channel, msg)

    # DM with an agent → that agent always responds.
    if is_dm:
        # Strip the `dm-` prefix to get the counterpart id.
        target = channel[len("dm-") :]
        if target in AGENT_IDS:
            asyncio.create_task(_run_agent_response(channel, target, text))
            return JSONResponse({"ok": True, "responded_by": [target]})
        return JSONResponse({"ok": True, "responded_by": []})

    # Channel: only respond if an agent in this channel is @-mentioned.
    mentioned = _resolve_mentions(text)
    channel_members = set(CHANNELS.get(channel, {}).get("members", []))
    responders = []
    missing = []
    for agent_id in mentioned:
        if agent_id in channel_members:
            responders.append(agent_id)
        else:
            missing.append(agent_id)

    if missing and not responders:
        # Tell the user where they could find the agent instead.
        present_in = []
        for agent_id in missing:
            chans = MEMBERS.get(agent_id, {}).get("channels", [])
            present_in.append(
                f"@{agent_id} is not a member of #{channel} — try "
                + ", ".join(f"#{c}" for c in chans)
                if chans
                else f"@{agent_id} is not a member of any channel"
            )
        await _post_system_message(channel, ". ".join(present_in))
        return JSONResponse({"ok": True, "responded_by": [], "missing": missing})

    for agent_id in responders:
        asyncio.create_task(_run_agent_response(channel, agent_id, text))
    return JSONResponse({"ok": True, "responded_by": responders})


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
