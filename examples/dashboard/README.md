# dashboard

A tiny internal dashboard that lets multiple users start and watch Agent on
Demand sessions through a browser, without each user holding an AoD token.

```
┌─────────┐   HTTP   ┌──────────┐   aod-sdk    ┌──────────────────┐
│ Browser │ ────────▶│ FastAPI  │ ────────────▶│ Agent on Demand  │
│         │◀──SSE────│ (this)   │◀──SSE────────│                  │
└─────────┘          └──────────┘              └──────────────────┘
```

The dashboard uses a **single service token** for all outbound calls. Your
own auth layer (VPN, SSO proxy, reverse-proxy header auth, etc.) goes in
front. See the [Internal Dashboard pattern](../../site/docs/patterns/dashboard.md)
for the full write-up.

## Install

```bash
pip install aod-sdk fastapi "uvicorn[standard]"
```

Python 3.11+ required.

## Configure

| Variable | Required | Default | What it does |
|---|---|---|---|
| `AOD_API_URL` | yes | — | Deployment URL |
| `AOD_API_TOKEN` | yes | — | Service token (`aod_...`) used for all outbound calls |
| `AOD_AGENT_ID` | yes | — | Shared agent — every dashboard user runs this one |
| `PORT` | no | `8000` | uvicorn bind port |

## Run

```bash
./app.py
# INFO: Uvicorn running on http://0.0.0.0:8000
```

Open <http://localhost:8000>. Type a prompt, hit **Start**, and the output
streams live into the log panel via a server-proxied `EventSource`.

## Routes

| Method | Path | Does |
|---|---|---|
| `GET`  | `/` | Dashboard HTML |
| `POST` | `/api/sessions` | Create a session `{prompt}` → `{id, status}` |
| `GET`  | `/api/sessions` | List sessions for the service token |
| `POST` | `/api/sessions/{id}/terminate` | Stop a running session |
| `GET`  | `/api/sessions/{id}/stream` | SSE proxy |

## What to look at

- `app.py:lifespan` — one `AsyncClient` for the process lifetime (reused
  across requests). The client reads `AOD_API_URL` and `AOD_API_TOKEN`
  from the environment.
- `app.py:stream` — the SSE proxy. `client.sessions.stream(...)` is an
  async context manager yielding typed `StreamEvent`s; we re-emit each
  payload as a `data:` line so the browser's `EventSource` sees a
  dashboard-native stream (the upstream AoD token never touches the
  browser).
- `app.py:terminate_session` — `ConflictError` (HTTP 409) from a
  double-stop is treated as idempotent success, which matches the UX
  expectation of the "Stop" button.
- `templates/index.html` — standalone HTML; no build step, no JS
  dependencies. The JS is one `EventSource` per active session, polling
  `/api/sessions` every 5s for the session list.

## Production notes

- **Auth.** This example skips the authentication layer. In a real deploy:
  (a) put it behind your SSO/VPN, or (b) add your own cookie/JWT middleware
  that populates `request.state.user` before any `/api/*` route.
- **Per-user audit.** A single service token means AoD sees all sessions
  as "dashboard." If you need per-user attribution in AoD, create one
  `aod_` token per user via `APIKey.create_key` and store the mapping in
  your own DB, then pick the right client per request.
- **Multiple agents.** Hardcoded to `AOD_AGENT_ID` for brevity. To let
  users pick an agent, add `GET /api/agents` returning
  `client.agents.list()`, and include `agent_id` in the start-session body.
- **Session cleanup.** AoD keeps session rows forever. Run a cron job
  that calls `client.sessions.delete(id)` on sessions older than your
  retention threshold (sessions cannot be deleted while
  `status == "running"`).
- **Load balancer timeouts.** SSE needs long-lived connections. If your
  LB defaults to 30s, either bump it or switch to the WebSocket relay
  variant (documented in the pattern page).
