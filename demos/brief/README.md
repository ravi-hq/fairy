# Brief — Concurrent Research Fleet Demo

A FastAPI demo showing how Brief fans out concurrent agent sessions across multiple research dimensions and assembles a structured brief in real time.

## What it demonstrates

1. User enters a research target (company, product, market)
2. **4 concurrent "agent sessions"** start simultaneously, each researching a different dimension:
   - Product & Positioning
   - Team & History
   - Customers & Market
   - Competitors & Landscape
3. Each dimension streams its findings in real time into its own panel
4. As dimensions complete, their panels show "✓ Complete"
5. Once all 4 are done, a **synthesis agent** runs and assembles the full brief
6. The final brief appears below — a structured document with sections

The concurrent streaming across 4 panels is the core demo moment — it shows the fleet in action.

## Pre-loaded targets

- **Linear** — project management tool for modern engineering teams
- **Resend** — email API for developers (React Email + sending infrastructure)
- **Fly.io** — global application deployment platform

Each target has hand-crafted, realistic mock research findings for all 4 dimensions.

## How to run

```bash
cd demos/brief
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000

## Tech stack

- **FastAPI** — web framework
- **sse-starlette** — Server-Sent Events support
- **asyncio.gather** — concurrent mock agent sessions
- **Pure HTML + vanilla JS** — no build step, no npm
- **Multiplexed SSE** — single `/research/stream` endpoint sends events for all 4 dimensions with a `session` field for client-side routing

## Architecture

```
/research/stream?target=Linear
    └── asyncio.gather(
            mock_research_stream("Linear", "product_positioning"),
            mock_research_stream("Linear", "team_history"),
            mock_research_stream("Linear", "customers_market"),
            mock_research_stream("Linear", "competitors_landscape"),
        )
        → asyncio.Queue → SSE stream
        → mock_synthesis_stream("Linear")
        → SSE stream end
```

Each SSE event is JSON with a `session` field (`product_positioning`, `team_history`, etc.) that the JS client uses to route the event to the correct panel.

## Connecting to real AoD

Replace `mock_research_stream()` in `app.py` with real AoD session calls. The event schema is already compatible — real sessions would yield the same `{"session": dim, "type": "output", "data": "..."}` shape.
