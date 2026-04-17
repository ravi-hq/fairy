# Pattern: Internal Dashboard

You want an internal web UI where multiple users can kick off and monitor fairy
sessions without each managing their own API token or knowing about the fairy
API.

## Shape of the solution

Your dashboard acts as an authenticated proxy between your users and fairy. Each
dashboard user is mapped to a fairy API token (or a shared service token with
per-user metadata). The dashboard:

1. Accepts user requests through your own auth layer.
2. Calls `POST /sessions` on the user's behalf using a fairy token.
3. Lists running and past sessions via `GET /sessions`.
4. Proxies the SSE stream to the browser — either by forwarding
   `GET /sessions/{id}/stream` directly or relaying events over a WebSocket.

## Authentication relay

The simplest approach is a **single service token** (one `fairy_` token per
fairy instance). Your dashboard controls who can call fairy — fairy only sees
your service token. Store the fairy token in your backend's secret store and
never expose it to browser clients.

If you need per-user audit trails in fairy, create one fairy token per user
(via `APIKey.create_key`) and store the mapping in your own database.

## Listing sessions

```python
import httpx

import os

FAIRY_URL = os.environ["FAIRY_URL"]
FAIRY_TOKEN = os.environ["FAIRY_TOKEN"]

def list_sessions() -> list[dict]:
    r = httpx.get(
        f"{FAIRY_URL}/sessions",
        headers={"Authorization": f"Bearer {FAIRY_TOKEN}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["data"]   # list of session objects, ordered by created_at desc
```

Each session object includes `id`, `status`, `runtime`, `created_at`,
`updated_at`, and `resources`.

## Streaming to the browser

**Option A — SSE proxy (simplest):** Your backend route opens
`GET /sessions/{id}/stream` from fairy and re-streams the response to the
browser with `Content-Type: text/event-stream`. The browser's `EventSource`
connects to your route, not to fairy directly.

**Option B — WebSocket relay:** Your backend opens the SSE stream from fairy,
collects events, and pushes them to the browser over a WebSocket. Use this when
your infrastructure doesn't support long-lived HTTP responses (e.g. behind an
API gateway with a 30-second timeout).

## Design notes

- **Session ownership:** All sessions created with the same fairy token are
  visible to `GET /sessions`. If you share one token across users, prefix
  session prompts or use the `metadata` field on agents to tag sessions by
  user.
- **Creating sessions:** `POST /sessions` requires `agent_id` — create one
  named agent per task type (e.g. "code-review", "doc-gen") and let the
  dashboard UI pick the right one.
- **Termination:** Provide a "Stop" button that calls
  `POST /sessions/{id}/terminate`. Fairy returns a `409` if the session is
  already terminated — handle it gracefully.
- **Cleanup:** Implement a retention policy: call
  `DELETE /sessions/{id}/delete` on sessions older than your threshold.
  Sessions cannot be deleted while `status == "running"`.

## Trade-offs

| | |
|---|---|
| **Token isolation** | A single service token simplifies ops but loses per-user audit trails in fairy. |
| **SSE proxy** | Straightforward, but requires long-lived connections — check your load balancer timeout settings. |
| **WebSocket relay** | More complex but works anywhere; lets you add server-side filtering or enrichment. |
| **Session listing** | `GET /sessions` returns all sessions for the token, newest first — add client-side filtering for large result sets. |
