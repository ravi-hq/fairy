# The Board That Ships

A kanban board POC where moving a ticket to **In Progress** spawns an AI agent
session that writes a PR and streams its output back to the UI in real time.

## How to run

```bash
cd demos/board-that-ships
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000 in your browser.

## What it does

1. Three pre-populated engineering tickets sit in the **Todo** column.
2. Click **Start Agent** on any ticket.
3. The ticket moves to **In Progress** and an agent panel opens on the right.
4. A mock AoD client streams provisioning stages and realistic agent output via SSE.
5. When the agent finishes it opens a fake PR; the ticket moves to **In Review**
   and the PR link appears on the card.

## Architecture

- **`app.py`** — FastAPI app with in-memory ticket state and an SSE streaming
  endpoint that drives the mock agent.
- **`static/index.html`** — Single-page kanban UI (vanilla JS, no build step).
- **`requirements.txt`** — `fastapi`, `uvicorn`, `sse-starlette`.

## Mock AoD client

The demo does **not** connect to the real AoD API. Instead, `mock_stream()`
in `app.py` yields `StreamEvent` objects (matching the real protocol) with
`asyncio.sleep` delays to simulate realistic latency:

- **`stage`** events update the provisioning badge row in the panel.
- **`output`** events stream text lines to the terminal view.
- **`exit`** event signals completion and triggers the PR URL extraction.
