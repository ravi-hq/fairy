# Relay

**Slack reimagined where agents are first-class teammates with persistence.**

Today's Slack bots are command-line tools in chat-clothing — stateless,
reactive, command-driven. Relay's agents have **presence** (idle / working /
done with a what-they're-doing tooltip), **persistent sessions** (their
filesystem stays warm between turns), and **proactive behavior** (they read
the room and respond to relevant signals without being summoned).

This is a self-contained POC with mock data — no real Agent on Demand or
LLM calls. It exists to dramatize the product shape, not to be wired up.

## Run it

```bash
cd demos/relay
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8910
```

Then open <http://localhost:8910>.

## What's on screen

Three columns, like Slack:

- **Left rail** — workspace (Acme Eng), search, channels, DMs, and a member
  list. Humans are circular avatars, agents are rounded squares with a robot
  glyph and a presence dot (`idle`, `working`, `awaiting_human`). The
  monospace sub-line under each agent shows what it's currently doing — hover
  for the full tooltip.
- **Main column** — the selected channel, with messages, threaded replies
  collapsed to "N replies", and a typing indicator while an agent streams.
  Agent messages are visually distinguished with a subtle accent border and
  a "Working..." badge while in flight.
- **Right rail** — only visible when a thread is open. Renders the original
  message plus replies, including agent-authored structured cards (the
  on-call brief, the competitor brief).

## The three pre-loaded scenes

Pick one from the **Run scene** dropdown above the timeline and hit **Play**.
The active channel switches automatically.

1. **`#on-call` — Alert fires.** A PagerDuty alert lands. Scout (the agent
   member of `#on-call`) flips from `idle` to `working: investigating
   checkout-service alert` without anyone @-mentioning it. Over ~10 seconds
   it streams investigation steps as a single message that updates in place
   — query metrics, check deploys, sample traces, diff the suspect commit.
   When done, it posts a structured brief as a thread reply on the alert:
   probable cause / evidence / blast radius / suggested action, each in a
   color-coded card. Status returns to `idle`.

2. **`#competitive-intel` — Competitor launch.** Sam posts a Notion launch
   link. Researcher reads the room, replies in-thread "On it.", and streams
   a competitor brief over ~12 seconds: positioning shift, pricing changes,
   a feature parity table (us vs them), and three implications for the
   roadmap.

3. **`#engineering` — Stuck on permission.** Maya tells Migrator to kick off
   the prod migration. Migrator runs pre-flight (schema, code references,
   `pg_stat_statements`, snapshot verification) — then **stops** and
   @-mentions @jake with the actual SQL it's about to run, what it checked,
   what it's still uncertain about, and a Proceed/Abort prompt. Status flips
   to `awaiting_human`.

## What to watch for

- **Presence**: the agent's status chip in the left rail flips on its own
  when work starts and lands, including the "what am I doing" sub-line.
- **Proactivity**: in scene 1, nobody summoned Scout. The alert *is* the
  trigger. Scene 2 is the same shape — Sam pasted a link, Researcher
  decided to respond.
- **Streaming as message-update**: the agent's investigation steps grow
  inside a single message rather than spamming the timeline. The frontend
  receives `message_update` patches over SSE and re-renders in place.
- **In-thread structured replies**: the on-call brief and the competitor
  brief render as cards inside the thread pane on the right, not as walls
  of plain text in the channel.
- **Junior-engineer escalation**: Migrator pauses and asks before doing
  something destructive, with the exact SQL, the checks it ran, and what
  it explicitly couldn't verify.

## Disclaimer

No real Agent on Demand calls are made. There are no LLM completions, no
Sprite VMs, no DB writes. Every "stream" is a pre-scripted sequence of
events in `scenarios.py` played back over SSE. This demo dramatizes what
agent-native chat would look like if you built it on AoD's primitives — it
is not the product.

## Files

- `app.py` — FastAPI app, in-memory state, SSE broadcast.
- `scenarios.py` — the three scenes, as ordered `(delay, kind, payload)`
  tuples.
- `static/index.html` — single-file vanilla-JS frontend with inline CSS,
  SVG icons, and one global `EventSource`.
- `requirements.txt` — `fastapi`, `uvicorn`, `sse-starlette`.
