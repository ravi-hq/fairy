# Streaming

## Endpoint

```
GET /sessions/{id}/stream
Authorization: Bearer <token>
```

Agent on Demand streams session output as [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events). The response has:

- `Content-Type: text/event-stream`
- `Cache-Control: no-cache`
- `X-Accel-Buffering: no`

Each event is a line of the form `data: <json>\n\n`, preceded by an `id: <int>\n` line for every event except `start`.

## Event types

| Type | Payload | Notes |
|------|---------|-------|
| `start` | `{"type":"start","runtime":"claude","session_id":"<uuid>"}` | Always the first event, before any replayed output. No `id` field. |
| `stage` | `{"type":"stage","id":3,"stage":"create_sprite","state":"started"\|"done"\|"failed","duration_ms":15200,"message":"..."}` | Emitted during provisioning and just before the runtime starts. `duration_ms` is present on `done` and `failed`; `message` is present on `failed` only. Non-terminal — clients should keep reading. See [Provisioning stages](#provisioning-stages) below. |
| `turn_start` | `{"type":"turn_start","id":42,"turn":1}` | Emitted before the first `output` event of each turn. Turn numbers start at 1. Does not advance the SSE cursor (`id:` line is omitted); use the `id` from the `output` event that follows for `Last-Event-ID`. |
| `output` | `{"type":"output","id":42,"stream":"stdout"\|"stderr","data":"...","turn":1}` | One chunk of agent output; may contain multiple lines |
| `exit` | `{"type":"exit","id":42,"code":0}` | Terminal. Emitted when the runtime exits (code 0 = success, non-zero = failure) |
| `error` | `{"type":"error","id":42,"message":"..."}` | Terminal. Emitted on unhandled exception — no exit code available |
| `terminated` | `{"type":"terminated","id":42,"message":"Session terminated"}` | Terminal. Emitted after `POST /sessions/{id}/terminate` |
| `stale` | `{"type":"stale","id":42,"message":"No output for 600s"}` | Terminal. Emitted if the server sees no new log chunks for 10 minutes on a still-`running` session. The session row may remain `running`; clients should treat this as terminal and reconnect if desired. |

Every event except `start` includes an `"id"` field in its JSON payload, set to the log row ID. For terminal events (`exit`, `error`, `terminated`, `stale`), `id` is set to the last seen log row.

The stream closes after any terminal event.

## Provisioning stages

Between `POST /sessions` returning `202` and the first `output` event arriving, AoD is creating a Sprite sandbox, running setup steps, and starting the runtime. `stage` events surface that work so clients can render "currently cloning ravi-hq/fairy…" instead of a generic waiting spinner.

Each stage that actually runs emits a `started` event on entry and a `done` event on clean exit (carrying `duration_ms`). Stages that are skipped (empty packages, no setup script, etc.) emit no events — absence means "not run." On failure, a `failed` event is emitted with `duration_ms` and a short `message`, followed by the session's terminal event (`error` in most cases).

Possible `stage` values:

| Stage | When it runs |
|-------|-------------|
| `create_sprite` | Always — first thing after `POST /sessions`. Typically the longest stage. |
| `network_policy` | Only when `environment.networking.type == "limited"`. |
| `env_file` | Always — writes the runtime API key and any `environment.env_vars` to the Sprite. |
| `git_credentials` | When at least one repo resource has an `authorization_token`. |
| `provision_setup` | Always — runs the batched provisioning script (chmod of pre-written files, package installs, git clones, user setup). Covers what used to be reported as `packages.*`, `clone_repos`, and `user_setup` separately; those discrete stages are no longer emitted. |
| `mcp_config` | When the agent has MCP servers configured. |
| `skills` | When the agent has skills configured. |
| `runtime_start` | `started` only — emitted just before the runtime CLI launches. `output` events follow once the runtime writes to stdout/stderr. |

Stage events are ordered by the same `id` sequence as `output` events; `Last-Event-ID` resume works across both.

## Heartbeats

Every 15 seconds with no output, Agent on Demand sends a heartbeat to keep the connection alive:

```
: heartbeat
```

**Skip any line that starts with `:`** — heartbeat lines are not JSON.

## Replay behavior

Connecting to a stream always replays all stored output from the beginning:

- **Session still running**: you get everything buffered so far, then live output as it arrives.
- **Session already terminal**: you get `start` → all buffered events → the terminal event, then the stream closes immediately.

## Example stream

```
data: {"type": "start", "runtime": "claude", "session_id": "..."}

data: {"type": "turn_start", "id": 1, "turn": 1}

id: 1
data: {"type": "output", "id": 1, "stream": "stdout", "data": "hello\n", "turn": 1}

id: 2
data: {"type": "output", "id": 2, "stream": "stdout", "data": "world\n", "turn": 1}

id: 2
data: {"type": "exit", "id": 2, "code": 0}
```

## Resuming a stream

Every event other than `start` carries an `id`. To resume after a disconnect,
pass the last `id` you received in either:

- The `Last-Event-ID` HTTP header (automatic for browser `EventSource` clients)
- A `?since=<id>` query parameter (useful for `fetch`, `requests`, or curl)

If both are supplied, the header wins.

The server resumes from the next event after the supplied `id`. If the event
no longer exists (for example, logs older than 30 days are purged), the
stream silently resumes from the nearest surviving event rather than failing.
Pass `since=0` (or omit) for a full replay.

If the `id` is not a non-negative integer, the server returns `400`.

## Client examples

=== "curl"

    The `-N` flag disables output buffering.

    ```bash
    curl -N \
      -H "Authorization: Bearer $TOKEN" \
      "$BASE/sessions/<session-uuid>/stream"
    ```

    To resume after a disconnect, pass the last `id` you received:

    ```bash
    curl -N \
      -H "Authorization: Bearer $TOKEN" \
      -H "Last-Event-ID: 42" \
      "$BASE/sessions/<session-uuid>/stream"
    ```

=== "Python (aod-sdk)"

    The [`aod-sdk`](../sdks/python.md) package handles SSE parsing, heartbeats, and `Last-Event-ID` resume for you. Events are typed `StreamEvent` objects; everything beyond `type` and `id` lands in `event.extra`.

    ```python
    from aod import Client

    with Client() as client:
        with client.sessions.stream(session_id) as events:
            for event in events:
                if event.type == "output":
                    print(event.extra["data"], end="")
                elif event.type == "stage":
                    print(f"[{event.extra['stage']} {event.extra['state']}]")
                elif event.type in ("exit", "error", "terminated", "stale"):
                    print(f"\n[{event.type}]")
                    break
    ```

    Pass `since=<id>` to resume after a previously-seen event:

    ```python
    with client.sessions.stream(session_id, since=42) as events:
        ...
    ```

=== "Python (raw)"

    If you'd rather not add `aod-sdk` as a dependency, here's a minimal reconnect-aware loop on top of `requests`:

    ```python
    import json
    import requests

    last_event_id = 0
    while True:
        headers = {"Authorization": f"Bearer {token}"}
        if last_event_id:
            headers["Last-Event-ID"] = str(last_event_id)
        with requests.get(url, headers=headers, stream=True) as r:
            for line in r.iter_lines(decode_unicode=True):
                if not line or line.startswith(":"):
                    continue  # blank line between events, or heartbeat
                if line.startswith("id: "):
                    last_event_id = int(line[4:])
                elif line.startswith("data: "):
                    event = json.loads(line[6:])
                    # handle event...
                    if event["type"] in ("exit", "error", "terminated", "stale"):
                        return
        # loop reconnects with Last-Event-ID preserved
    ```
