# Scout — Incident Response Co-pilot

Scout is a demo / POC showing what an AI-powered on-call co-pilot looks like.
When an alert fires, Scout immediately spawns an agent session, runs the full
investigation playbook, and posts a structured brief — before the on-call
engineer is ever paged.

## How to run

```bash
cd demos/scout
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000 in your browser.

## What you'll see

- **Left panel** — an alert inbox (PagerDuty-lite).  Each fired alert appears
  with a severity badge (P1/P2/P3), service name, description, timestamp, and
  a live "Investigating…" status pill that flips to "Complete" when done.

- **Right panel** — real-time investigation output streaming in a
  terminal-style window, followed by a colour-coded structured brief:
  - Probable Cause (red)
  - Evidence (blue)
  - Blast Radius (orange)
  - Suggested Action (green)

## Pre-loaded scenarios

Press "Fire Test Alert" to cycle through three scenarios:

| # | Alert | Service | Priority |
|---|-------|---------|----------|
| 1 | Error rate spike on /api/checkout | checkout-service | P1 |
| 2 | p99 latency > 2000 ms on /api/feed | feed-service | P2 |
| 3 | Worker pod OOMKilled (3× in 10 min) | worker-service | P2 |

Each scenario runs a realistic, multi-step investigation with asyncio delays
to simulate a live agent session.

## Architecture

```
app.py          FastAPI app — alert endpoints, SSE streaming, in-memory state
scenarios.py    Three mock alert scenarios with investigation steps and briefs
static/
  index.html    Split-panel UI (vanilla JS, SSE, no build step)
requirements.txt
```

No database is used — all state is in-memory. The mock AoD client
(`mock_investigate` in `app.py`) simulates provisioning stages and
investigation output with `asyncio.sleep` delays, so no real API keys are
needed.
