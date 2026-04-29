#!/usr/bin/env python3
"""bot.py — a Slack bot that runs Agent on Demand sessions per thread.

One thread → one session. The first message in a thread creates a session;
every subsequent message in the thread resumes it via
`client.sessions.prompt(id, prompt=...)`, so the agent keeps full context
across turns.

Run with Slack's socket mode so the bot doesn't need a public URL.

Required env vars:
    SLACK_BOT_TOKEN    xoxb-... (from your Slack app's OAuth page)
    SLACK_APP_TOKEN    xapp-... (from the App-Level Tokens section, needs
                       `connections:write` scope for socket mode)
    AOD_API_URL        Deployment URL
    AOD_API_TOKEN      aod_... token
    AOD_AGENT_ID       Agent that handles messages in every thread
"""

from __future__ import annotations

import logging
import os

from aod import AodError, Client, ConflictError
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chat-bot")

AGENT_ID = os.environ["AOD_AGENT_ID"]

app = App(token=os.environ["SLACK_BOT_TOKEN"])
client = Client()  # reads AOD_API_URL + AOD_API_TOKEN

# Simple in-memory store; use Redis/DB in production so restarts don't drop state.
thread_sessions: dict[str, str] = {}


def _drain_stdout(session_id: str, turn: int) -> str:
    """Block until the session reaches a terminal event, returning this turn's stdout joined.

    Filters by ``turn`` because ``client.sessions.stream`` replays from the
    start of the session; without the filter, every reply after the first
    would include all prior turns concatenated.
    """
    parts: list[str] = []
    with client.sessions.stream(session_id) as events:
        for event in events:
            if (
                event.type == "output"
                and event.extra.get("stream") == "stdout"
                and event.extra.get("turn") == turn
            ):
                parts.append(event.extra.get("data", ""))
            elif event.type in ("exit", "error", "terminated", "stale"):
                break
    text = "".join(parts).strip()
    return text or "_(no output)_"


@app.event("app_mention")
def on_mention(event: dict, say) -> None:
    thread_ts = event.get("thread_ts") or event["ts"]
    # Strip the @bot mention from the prompt — Slack includes it verbatim.
    prompt = event["text"].split(">", 1)[-1].strip() or "(no prompt)"

    try:
        if thread_ts not in thread_sessions:
            ack = client.sessions.create(agent_id=AGENT_ID, prompt=prompt)
            thread_sessions[thread_ts] = str(ack.id)
            log.info("new session %s for thread %s", ack.id, thread_ts)
        else:
            session_id = thread_sessions[thread_ts]
            try:
                ack = client.sessions.prompt(session_id, prompt=prompt)
                log.info("resumed session %s for thread %s", session_id, thread_ts)
            except ConflictError:
                # 409: session is running (user typed during a reply).
                # Simple strategy: tell the user; robust strategy: enqueue.
                say(text="Still working on the previous message…", thread_ts=thread_ts)
                return

        reply = _drain_stdout(thread_sessions[thread_ts], ack.current_turn)
    except AodError as e:
        log.exception("aod error")
        say(text=f":warning: agent error: {e}", thread_ts=thread_ts)
        return

    say(text=reply, thread_ts=thread_ts)


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    log.info("connecting to Slack in socket mode…")
    handler.start()
