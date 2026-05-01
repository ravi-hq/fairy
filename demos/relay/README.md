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

- **Left rail** — workspace (Acme Eng), quick switcher, channels with unread
  badges, DMs, member list with presence + activity, and an **Ambient
  activity** ticker that auto-cycles through what's happening across the
  workspace. Humans are circular avatars, agents are rounded squares with a
  robot glyph.
- **Main column** — the selected channel or DM, with messages, threaded
  replies, a typing indicator while an agent streams, and a working composer
  at the bottom.
- **Right rail** — only visible when a thread is open. Renders the original
  message plus replies, including agent-authored structured cards.

## You can drive it

The composer is real. Three input modes:

1. **Plain text** — posts as you (Jake) into the active channel. No agent
   responds unless you @-mention one.
2. **@-mention an agent** — `@scout investigate the 502 spike` flips Scout
   to `working`, streams 3–5 status sub-lines, and posts a structured reply
   (a brief, a parity table, a migration report — depending on the agent and
   keywords). If the agent isn't a member of the current channel, you get
   a system message telling you where to find them.
3. **Slash commands** — type `/` to open an autocomplete popover. Tab or
   Enter accepts. Available:
    - `/scout investigate <query>` — explicit dispatch in any channel
    - `/researcher brief <topic>` — brief on a topic
    - `/migrator preflight <table>` — pre-flight scan with a changes table
    - `/poll <question> | opt1 | opt2 | …` — poll with vote buttons
    - `/dm <agent>` — open or jump to a DM
    - `/help` — list the commands

In a DM with an agent (Scout, Researcher), **every** message gets a
response — no @-mention required. DMs with humans (Maya, Sam) are passive.

Click any agent or human name (sidebar, message author, mention) to open a
**profile modal** with their role, status, system prompt, tools, channels,
and recent activity. For agents the modal carries a "Made in Forge" badge
linking out to where the agent was authored.

Press **⌘K / Ctrl-K** for the **quick switcher** — fuzzy search across
channels and DMs, Enter to jump.

When an agent posts to a channel that isn't currently active, a small
**toast** appears bottom-right with a "Jump to thread" button.

## The three pre-loaded scenes

Pick one from the **Run scene** dropdown above the timeline and hit **Play**.
The active channel switches automatically. These remain unchanged from the
original POC.

1. **`#on-call` — Alert fires.** A PagerDuty alert lands. Scout flips from
   `idle` to `working: investigating checkout-service alert` without anyone
   @-mentioning it. Streams investigation steps, posts a structured brief
   as a thread reply: probable cause / evidence / blast radius / suggested
   action.

2. **`#competitive-intel` — Competitor launch.** Sam posts a Notion launch
   link. Researcher reads the room, replies in-thread, and streams a
   competitor brief: positioning shift, pricing changes, feature parity
   table, and roadmap implications.

3. **`#engineering` — Stuck on permission.** Maya tells Migrator to kick off
   the prod migration. Migrator runs pre-flight, then **stops** and
   @-mentions @jake with the actual SQL it's about to run, what it checked,
   what it's still uncertain about, and a Proceed/Abort prompt. Status flips
   to `awaiting_human`.

## What to watch for

- **Presence**: the agent's status chip in the left rail flips on its own
  when work starts and lands.
- **Proactivity**: in scene 1, nobody summoned Scout. The alert *is* the
  trigger.
- **Streaming as message-update**: investigation steps grow inside a single
  message rather than spamming the timeline.
- **In-thread structured replies**: briefs, parity tables, migration reports,
  and PR reviews render as rich cards, not walls of plain text.
- **Junior-engineer escalation**: Migrator pauses and asks before doing
  something destructive, with the exact SQL.
- **Lived-in workspace**: every channel ships with a few hours of realistic
  backstory — release notes, deploy emoji, a flaky-test thread, OKR pulses,
  competitor scans. Channels and DMs aren't empty when you walk in.

## Disclaimer

No real Agent on Demand calls are made. There are no LLM completions, no
Sprite VMs, no DB writes. Every "stream" is a pre-scripted sequence of
events in `scenarios.py` played back over SSE. This demo dramatizes what
agent-native chat would look like if you built it on AoD's primitives — it
is not the product.

## Files

- `app.py` — FastAPI app, in-memory state, SSE broadcast, agent responder
  engine, slash-command dispatch, profile endpoint.
- `scenarios.py` — the three scripted scenes, the keyword-keyed
  `AGENT_RESPONDERS` table that drives @-mentions and slash commands,
  `AGENT_PROFILES` for the profile modal, `SEED_HISTORY` for channel
  backstory, `SEED_DMS` for pre-populated DM threads, `AMBIENT_ACTIVITY`
  for the sidebar ticker, and `INITIAL_UNREADS`.
- `static/index.html` — single-file vanilla-JS frontend with inline CSS,
  SVG icons, and one global `EventSource`.
- `requirements.txt` — `fastapi`, `uvicorn`, `sse-starlette`.
