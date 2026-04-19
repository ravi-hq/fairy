# Pattern: Chat Bot

You want users to converse with an AI agent through Slack, Discord, or an
internal chat tool — with each message in a thread continuing the same session.

## Shape of the solution

Map **one chat thread → one Agent on Demand session**. On the first message in a thread,
call `POST /sessions` to create a session and store the returned `id` alongside
the thread ID in your bot's storage. On every subsequent message in that thread,
call `POST /sessions/{id}/prompt` — Agent on Demand resumes the same Sprite, so the agent
has full context of the prior conversation.

Stream the agent's response back to the thread via
`GET /sessions/{id}/stream`.

## Example (Slack)

```python
import httpx
from slack_bolt import App

import os

app = App(token=os.environ["SLACK_BOT_TOKEN"])
AOD_URL = os.environ["AOD_URL"]
AOD_TOKEN = os.environ["AOD_TOKEN"]
AGENT_ID = os.environ["AOD_AGENT_ID"]

# Simple in-memory store; use Redis/DB in production
thread_sessions: dict[str, str] = {}

def api_headers():
    return {"Authorization": f"Bearer {AOD_TOKEN}"}

def create_session(prompt: str) -> str:
    r = httpx.post(
        f"{AOD_URL}/sessions",
        json={"agent_id": AGENT_ID, "prompt": prompt},
        headers=api_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]

def send_prompt(session_id: str, prompt: str) -> str:
    r = httpx.post(
        f"{AOD_URL}/sessions/{session_id}/prompt",
        json={"prompt": prompt},
        headers=api_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]

def collect_output(session_id: str) -> str:
    lines = []
    with httpx.stream(
        "GET",
        f"{AOD_URL}/sessions/{session_id}/stream",
        headers={**api_headers(), "Accept": "text/event-stream"},
        timeout=None,
    ) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                lines.append(line[6:])
    return "\n".join(lines)

@app.event("app_mention")
def handle_mention(event, say):
    thread_ts = event.get("thread_ts") or event["ts"]
    prompt = event["text"]

    if thread_ts not in thread_sessions:
        session_id = create_session(prompt)
        thread_sessions[thread_ts] = session_id
    else:
        session_id = thread_sessions[thread_ts]
        send_prompt(session_id, prompt)

    output = collect_output(session_id)
    say(text=output, thread_ts=thread_ts)
```

## Trade-offs

| | |
|---|---|
| **Stateful threads** | Agent on Demand holds the session state; your bot only stores the `session_id` mapping. |
| **Multi-turn** | `POST /sessions/{id}/prompt` re-enters the Sprite — the agent sees previous output. |
| **Session lifetime** | Sprites are long-lived within a session; terminate (`POST /sessions/{id}/terminate`) when the thread is archived or idle. |
| **Concurrency** | `POST /sessions/{id}/prompt` returns `409` if the session is already running — queue incoming messages per thread if users type quickly. |
| **Storage** | In production, persist the `thread_ts → session_id` map in Redis or a database so it survives bot restarts. |
