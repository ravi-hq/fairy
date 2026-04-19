# Pattern: Internal Dashboard

You want an internal web UI where multiple users can kick off and monitor Agent on Demand
sessions without each managing their own API token or knowing about the Agent on Demand
API.

## Shape of the solution

Your dashboard acts as an authenticated proxy between your users and Agent on Demand. Each
dashboard user is mapped to a Agent on Demand API token (or a shared service token with
per-user metadata). The dashboard:

1. Accepts user requests through your own auth layer.
2. Calls `POST /sessions` on the user's behalf using a Agent on Demand token.
3. Lists running and past sessions via `GET /sessions`.
4. Proxies the SSE stream to the browser — either by forwarding
   `GET /sessions/{id}/stream` directly or relaying events over a WebSocket.

## Authentication relay

The simplest approach is a **single service token** (one `aod_` token per
Agent on Demand instance). Your dashboard controls who can call Agent on Demand — Agent on Demand only sees
your service token. Store the Agent on Demand token in your backend's secret store and
never expose it to browser clients.

If you need per-user audit trails in Agent on Demand, create one Agent on Demand token per user
(via `APIKey.create_key`) and store the mapping in your own database.

## Listing sessions

```python
import httpx

import os

AOD_URL = os.environ["AOD_URL"]
AOD_TOKEN = os.environ["AOD_TOKEN"]

def list_sessions() -> list[dict]:
    r = httpx.get(
        f"{AOD_URL}/sessions",
        headers={"Authorization": f"Bearer {AOD_TOKEN}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]   # list of session objects, ordered by created_at desc
```

Each session object includes `id`, `status`, `runtime`, `created_at`,
`updated_at`, and `resources`.

## Streaming to the browser

**Option A — SSE proxy (simplest):** Your backend route opens
`GET /sessions/{id}/stream` from Agent on Demand and re-streams the response to the
browser with `Content-Type: text/event-stream`. The browser's `EventSource`
connects to your route, not to Agent on Demand directly.

**Option B — WebSocket relay:** Your backend opens the SSE stream from Agent on Demand,
collects events, and pushes them to the browser over a WebSocket. Use this when
your infrastructure doesn't support long-lived HTTP responses (e.g. behind an
API gateway with a 30-second timeout).

## Design notes

- **Session ownership:** All sessions created with the same Agent on Demand token are
  visible to `GET /sessions`. If you share one token across users, prefix
  session prompts or use the `metadata` field on agents to tag sessions by
  user.
- **Creating sessions:** `POST /sessions` requires `agent_id` — create one
  named agent per task type (e.g. "code-review", "doc-gen") and let the
  dashboard UI pick the right one.
- **Termination:** Provide a "Stop" button that calls
  `POST /sessions/{id}/terminate`. Agent on Demand returns a `409` if the session is
  already terminated — handle it gracefully.
- **Cleanup:** Implement a retention policy: call
  `DELETE /sessions/{id}/delete` on sessions older than your threshold.
  Sessions cannot be deleted while `status == "running"`.

## Trade-offs

| | |
|---|---|
| **Token isolation** | A single service token simplifies ops but loses per-user audit trails in Agent on Demand. |
| **SSE proxy** | Straightforward, but requires long-lived connections — check your load balancer timeout settings. |
| **WebSocket relay** | More complex but works anywhere; lets you add server-side filtering or enrichment. |
| **Session listing** | `GET /sessions` returns all sessions for the token, newest first — add client-side filtering for large result sets. |
