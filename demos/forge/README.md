# Forge — team agent maker

Forge is the social layer on top of Agent on Demand. AoD gives you the
primitive — agent + environment + session. Forge gives a team a place to
**publish, fork, rate, audit, and create** those agents the way a
company's internal App Store would. A PM defines `competitive-researcher`,
tests it, ships it; engineers get to see who is running what and what it
costs. No-code done right — not because complexity is hidden, but
because the underlying primitive is powerful enough that you don't need
code.

This is a self-contained POC with mock data. No real Agent on Demand
calls happen.

## How to run

```bash
cd demos/forge
pip install -r requirements.txt
uvicorn app:app --reload --port 8090
```

Then open http://localhost:8090.

## What you'll see

A workspace switcher, notification bell, and user pill at the top, a
footer-style stats strip below the tabs, and three top-level tabs.

### Library

A grid of eight published team agents. Above the grid:

- A **search box** that filters by name + description as you type.
- **Category pills** (All / Engineering / Research / Ops / Growth) — sticky
  filter; one active at a time.
- A **sort dropdown** with Most used / Top rated / Recently created /
  Most forked.
- A **+ New agent** button that jumps to the Create tab.

Each card shows the agent name, a category badge, owner, version,
weekly run count, rating, fork count, and the tools it uses. Click a
card to open the detail view.

### Detail

Three columns at the top — definition (system prompt, tool toggles,
"done when" criteria), a test panel that streams a real-feeling SSE run
when you click **Run test**, and a right rail with version history and
the fork tree. Below those columns, a full-width **Run history** table
with one row per past run (Time, Triggered by, Prompt, Duration, Cost,
Status). Click a row to open a modal showing the prompt + output, with
tabs for **Output**, **Tool calls**, and **Sandbox events**. The
modal's **Replay this run** button re-streams the agent's mock run
into the Output pane.

The **Tools** panel has a **? Browse all tools** link that opens the
**Tool catalog** modal — a list of every available tool with its
description, sensitivity, an example invocation, and a "Used by N agents"
deep-link back to the library filtered by that tool.

The **Fork** button at the top opens a modal where you can rename, edit
the system prompt, and toggle tools — saves are in-memory only.

### Create

A six-step wizard for publishing a new agent into the library:

1. **Basics** — name, description, category (Engineering / Research /
   Ops / Growth / Other), owner.
2. **System prompt** — large monospace textarea with helper text.
3. **Tools** — grid of tool cards with checkboxes. Bash, Filesystem, and
   Git carry a **Sensitive** badge and prompt a confirm before being
   enabled.
4. **Done criteria** — a short, testable criterion.
5. **Test** — runs the generic 6-step test sequence (Reading system
   prompt, Validating tools, Sandbox provisioned, Running test prompt,
   Output captured, Done) in the same monospace stream pane the Detail
   view uses.
6. **Publish** — a confirmation summary with a **Publish v1 to library**
   button. On publish, the agent is added to the in-memory library and
   you land on the new agent's Detail view.

The stepper at the top lets you click back to any step you've completed.

### Audit

Above the existing tables:

- Four **KPI cards** — Total runs this week, Total spend this week, Avg
  run duration, and Top spending agent.
- A **14-day stacked bar chart** of spend, one bar per day, segments
  colored by agent. Hover a bar for a date + per-agent breakdown +
  total.

The existing per-agent and per-human tables, plus the recent activity
log, sit below the chart.

## Pre-loaded agents

| Agent | Owner | Version | Category | Tools |
| --- | --- | --- | --- | --- |
| `competitive-researcher` | Maya M. | v3 | Research    | Web Search, Web Fetch |
| `pr-reviewer`            | Jake G. | v2 | Engineering | Filesystem, Bash, Git |
| `on-call-scout`          | Maya M. | v1 | Ops         | Honeycomb, GitHub, Slack |
| `changelog-writer`       | Sam P.  | v2 | Engineering | Git, Filesystem |
| `spec-drafter`           | Sam P.  | v1 | Engineering | Web Fetch, Linear, Notion |
| `dependency-bumper`      | Jake G. | v4 | Engineering | Filesystem, Bash, Git |
| `onboarding-buddy`       | Sam P.  | v1 | Growth      | Slack, Notion |
| `incident-comms`         | Maya M. | v2 | Ops         | Slack |

Each one has a hand-written system prompt, a "done when" criterion, and
a distinct mock test run — `pr-reviewer` outputs categorized findings,
`changelog-writer` emits three differently-formatted markdown files,
`on-call-scout` runs a Honeycomb-then-GitHub investigation,
`onboarding-buddy` schedules a new hire's first week, `incident-comms`
drafts a Statuspage update, and so on.

## Architecture

```
   Browser (vanilla JS, SSE)
      |
      | GET  /api/agents               -> library cards
      | POST /api/agents               -> create new agent (wizard)
      | GET  /api/agents/{id}          -> detail payload
      | GET  /api/agents/{id}/runs     -> run history
      | GET  /api/agents/{id}/runs/{r} -> run detail (modal)
      | POST /api/agents/{id}/test     -> { run_id }
      | GET  /api/runs/{id}/stream     -> SSE replay
      | POST /api/agents/{id}/fork
      | GET  /api/audit                -> totals + KPIs + 14-day spend
      | GET  /api/tools                -> tool catalog (browser modal)
      | GET  /api/footer                -> top-level stats strip
      v
   FastAPI app (app.py)
      |
      |  in-memory:
      |    AGENTS                  -> library (newly-created agents are appended)
      |    _runs[run_id]           -> { status, events: [...] }
      |    _subscribers[run_id]    -> [asyncio.Queue, ...]
      |
      |  for each /test call:
      |    asyncio.create_task(_mock_test_run(run_id, agent_id))
      |    -> drips scenarios.AGENTS[agent_id]['mock_test_run'] (or the
      |       generic 6-step fallback for newly-created agents)
      v
   scenarios.py
     AGENTS, RUN_HISTORY, DAILY_SPEND, TOOLS, AUDIT
     get_test_run() falls back to _generic_test_run() for agents
     created at runtime through the wizard.
```

## No real AoD calls

This demo never opens a Sprite, never calls a model, never writes to a
database. Every test run, fork, audit number, and run-history row is a
fixture in `scenarios.py`. It exists to show what the surface of a
Forge product would feel like sitting on top of Agent on Demand — not
to actually execute anything.
