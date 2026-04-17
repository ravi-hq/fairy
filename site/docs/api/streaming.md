# Streaming

## Endpoint

```
GET /sessions/{id}/stream
Authorization: Bearer <token>
```

Fairy streams session output as [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events). The response has:

- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`

Each event is a line of the form `data: <json>\n\n`.

## Event types

| Type | Payload | Notes |
|------|---------|-------|
| `start` | `{"type":"start","runtime":"claude","session_id":"<uuid>"}` | Always the first event, before any replayed output |
| `output` | `{"type":"output","stream":"stdout"\|"stderr","data":"..."}` | One chunk of agent output; may contain multiple lines |
| `exit` | `{"type":"exit","code":0}` | Terminal. Emitted when the runtime exits (code 0 = success, non-zero = failure) |
| `error` | `{"type":"error","message":"..."}` | Terminal. Emitted on unhandled exception — no exit code available |
| `terminated` | `{"type":"terminated","message":"Session terminated"}` | Terminal. Emitted after `POST /sessions/{id}/terminate` |

The stream closes after the terminal event (`exit`, `error`, or `terminated`).

## Heartbeats

Every 15 seconds with no output, fairy sends a heartbeat to keep the connection alive:

```
: heartbeat
```

**Skip any line that starts with `:`.**

## Replay behavior

Connecting to a stream always replays all stored output from the beginning:

- **Session still running**: you get everything buffered so far, then live output as it arrives.
- **Session already terminal**: you get `start` → all buffered `output` events → the terminal event, then the stream closes immediately.

This means you can safely disconnect and reconnect — you won't miss output.

## Example stream

```
data: {"type":"start","runtime":"claude","session_id":"<uuid>"}

data: {"type":"output","stream":"stdout","data":"Hello from the agent\n"}

data: {"type":"output","stream":"stdout","data":"Done.\n"}

data: {"type":"exit","code":0}
```

## Client examples

### curl

```bash
curl -N \
  -H "Authorization: Bearer $TOKEN" \
  "$BASE/sessions/<session-uuid>/stream"
```

The `-N` flag disables output buffering.

### Python

```python
import json
import requests

resp = requests.get(
    f"{BASE}/sessions/{session_id}/stream",
    headers={"Authorization": f"Bearer {TOKEN}"},
    stream=True,
)
resp.raise_for_status()

for line in resp.iter_lines(decode_unicode=True):
    if not line or line.startswith(":"):
        continue  # blank line between events, or heartbeat
    if not line.startswith("data: "):
        continue  # defensive
    event = json.loads(line[6:])
    if event["type"] == "output":
        print(event["data"], end="")
    elif event["type"] in ("exit", "error", "terminated"):
        print(f"\n[{event['type']}] {event.get('code', event.get('message', ''))}")
        break
```

## Reconnect guidance

Because fairy replays all output on every new connection, reconnecting is safe and cheap:

1. On connection error, wait briefly, then reconnect with the same request.
2. You will receive duplicate output starting from `start`, but you won't miss anything.

If you want to deduplicate, track how many `output` events you've received and skip that many at the start of a reconnect. Since `start` is always first and output events are append-only, counting is reliable.
