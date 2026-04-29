# Pattern: Chat Bot

You want users to converse with an AI agent through Slack, Discord, or an
internal chat tool — with each message in a thread continuing the same session.

Python examples use the official [`aod-sdk`](../sdks/python.md) package
(`pip install aod-sdk`).

## Shape of the solution

Map **one chat thread → one Agent on Demand session**. On the first message in a thread,
call `client.sessions.create(...)` and store the returned `id` alongside the
thread ID in your bot's storage. On every subsequent message in that thread,
call `client.sessions.prompt(session_id, ...)` — Agent on Demand resumes the
same Sprite, so the agent has full context of the prior conversation.

Stream the agent's response back to the thread via
`client.sessions.stream(session_id)`.

## Example (Slack)

!!! warning "Filter the stream by turn"
    `client.sessions.stream(session_id)` replays every `output` event from the
    start of the session, and the `exit`/`error`/`terminated` events are
    *session*-terminal, not per-turn. If you don't filter, every reply after
    the first will include all prior turns concatenated. Capture
    `ack.current_turn` from the create/prompt response and only emit
    `output` events whose `event.extra["turn"]` matches.

```python
import os
from aod import Client, ConflictError
from slack_bolt import App

app = App(token=os.environ["SLACK_BOT_TOKEN"])
AGENT_ID = os.environ["AOD_AGENT_ID"]
client = Client()  # reads AOD_API_URL + AOD_API_TOKEN

# Simple in-memory store; use Redis/DB in production
thread_sessions: dict[str, str] = {}

def run_turn(session_id: str, turn: int) -> str:
    """Collect this turn's stdout into a single reply string."""
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
    return "".join(parts)

@app.event("app_mention")
def handle_mention(event, say):
    thread_ts = event.get("thread_ts") or event["ts"]
    prompt = event["text"]

    if thread_ts not in thread_sessions:
        ack = client.sessions.create(agent_id=AGENT_ID, prompt=prompt)
        thread_sessions[thread_ts] = str(ack.id)
    else:
        session_id = thread_sessions[thread_ts]
        try:
            ack = client.sessions.prompt(session_id, prompt=prompt)
        except ConflictError as e:
            # 409 has two causes:
            #   running/pending — agent is still executing (user typed quickly)
            #   failed/terminated — session ended and cannot be resumed
            if "failed" in e.detail or "terminated" in e.detail:
                # Start a fresh session instead of resuming
                del thread_sessions[thread_ts]
                ack = client.sessions.create(agent_id=AGENT_ID, prompt=prompt)
                thread_sessions[thread_ts] = str(ack.id)
            else:
                say(text="Still working on the previous message…", thread_ts=thread_ts)
                return

    output = run_turn(thread_sessions[thread_ts], ack.current_turn)
    say(text=output, thread_ts=thread_ts)
```

A complete runnable implementation — socket-mode setup, `ConflictError`
handling, structured logging — lives at
[`examples/chat-bot/`](https://github.com/ravi-hq/agent-on-demand/tree/main/examples/chat-bot)
in the repo.

## Trade-offs

| | |
|---|---|
| **Stateful threads** | Agent on Demand holds the session state; your bot only stores the `session_id` mapping. |
| **Multi-turn** | `client.sessions.prompt(id, prompt=...)` re-enters the Sprite — the agent sees previous output. |
| **Session lifetime** | Sprites are long-lived within a session; call `client.sessions.terminate(id)` when the thread is archived or idle. |
| **Concurrency** | `prompt()` raises `ConflictError` (HTTP 409) if the session is `running` or `pending` (user typed quickly), or if it has `failed` or `terminated` (cannot be resumed). Check `e.detail` to distinguish. |
| **Storage** | In production, persist the `thread_ts → session_id` map in Redis or a database so it survives bot restarts. |
