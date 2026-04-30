# Forge — team agent maker

Forge is the social layer on top of Agent on Demand. AoD gives you the
primitive — agent + environment + session. Forge gives a team a place to
**publish, fork, rate, and audit** those agents the way a company's
internal App Store would. A PM defines `competitive-researcher`, tests
it, ships it; engineers get to see who is running what and what it
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

Three tabs at the top of the page.

- **Library** (default) — a grid of six published team agents. Each card
  shows the agent name, owner, version, weekly run count, rating, fork
  count, and the tools it uses. Click a card to open the detail view.
- **Detail** — a three-column inspector for one agent: the definition
  (system prompt, tool toggles, "done when" criteria), a test panel that
  streams a real-feeling SSE run when you click **Run test**, and a
  right rail with version history, fork tree, and recent runs. The
  **Fork** button at the top opens a modal where you can rename, edit
  the system prompt, and toggle tools — saves are in-memory only.
- **Audit** — totals for the week (runs, cost), a breakdown by agent
  and by human, a per-agent run-count bar, and a recent activity log.

## Pre-loaded agents

| Agent | Owner | Version | Tools |
| --- | --- | --- | --- |
| `competitive-researcher` | Maya M. | v3 | Web Search, Web Fetch |
| `pr-reviewer` | Jake G. | v2 | Filesystem, Bash, Git |
| `on-call-scout` | Maya M. | v1 | Honeycomb, GitHub, Slack |
| `changelog-writer` | Sam P. | v2 | Git, Filesystem |
| `spec-drafter` | Sam P. | v1 | Web Fetch, Linear, Notion |
| `dependency-bumper` | Jake G. | v4 | Filesystem, Bash, Git |

Each one has a hand-written system prompt, a "done when" criterion, and
a distinct mock test run — `pr-reviewer` outputs categorized findings,
`changelog-writer` emits three differently-formatted markdown files,
`on-call-scout` runs a Honeycomb-then-GitHub investigation, and so on.

## Architecture

```
   Browser (vanilla JS, SSE)
      |
      | GET /api/agents          -> library cards
      | GET /api/agents/{id}     -> detail payload
      | POST /api/agents/{id}/test
      |   -> { run_id }
      | GET /api/runs/{id}/stream  (text/event-stream)
      |   -> stage / tool / thought / output / result events
      | POST /api/agents/{id}/fork
      | GET /api/audit
      v
   FastAPI app (app.py)
      |
      |  in-memory:
      |    _runs[run_id]        -> { status, events: [...] }
      |    _subscribers[run_id] -> [asyncio.Queue, ...]
      |    _forks               -> [...]
      |
      |  for each /test call:
      |    asyncio.create_task(_mock_test_run(run_id, agent_id))
      |    -> drips scenarios.AGENTS[agent_id]['mock_test_run']
      |       onto every subscriber queue with realistic delays
      v
   scenarios.py
     AGENTS = { id -> { system_prompt, tools, done_when,
                        version_history, forks, recent_runs,
                        mock_test_run: [(delay, kind, text), ...] } }
     AUDIT  = { window, total_runs, total_cost_usd, by_agent, by_human, ... }
```

## No real AoD calls

This demo never opens a Sprite, never calls a model, never writes to a
database. Every test run, fork, and audit number is a fixture in
`scenarios.py`. It exists to show what the surface of a Forge product
would feel like sitting on top of Agent on Demand — not to actually
execute anything.
