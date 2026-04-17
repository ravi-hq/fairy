# Pattern: CLI Wrapper

Your team needs a one-liner that kicks off an AI coding task without opening a
browser or writing HTTP boilerplate.

## Shape of the solution

Wrap fairy in a thin CLI that creates a session via `POST /sessions`, then opens
the SSE stream at `GET /sessions/{id}/stream` and prints each event to the
terminal. The user gets a familiar `mytool code "<prompt>"` interface backed by
a real agent session.

Because fairy tracks session state, the CLI can also support follow-up prompts
(`POST /sessions/{id}/prompt`) — but the simplest starting point is
single-shot: create → stream → exit.

## Example

```python
#!/usr/bin/env python3
"""mytool code <prompt> — run a fairy session and stream output."""
import sys
import httpx

import os

FAIRY_URL = os.environ["FAIRY_URL"]        # e.g. https://fairy.example.com
FAIRY_TOKEN = os.environ["FAIRY_TOKEN"]    # fairy_...
AGENT_ID = os.environ["FAIRY_AGENT_ID"]   # your team's shared agent

def main():
    if len(sys.argv) < 2:
        print("usage: mytool code '<prompt>'", file=sys.stderr)
        sys.exit(1)

    prompt = " ".join(sys.argv[1:])
    headers = {"Authorization": f"Bearer {FAIRY_TOKEN}"}

    # 1. Create session
    resp = httpx.post(
        f"{FAIRY_URL}/sessions",
        json={"agent_id": AGENT_ID, "prompt": prompt},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    session = resp.json()
    session_id = session["id"]
    print(f"# session {session_id}", flush=True)

    # 2. Stream output
    with httpx.stream(
        "GET",
        f"{FAIRY_URL}/sessions/{session_id}/stream",
        headers={**headers, "Accept": "text/event-stream"},
        timeout=None,
    ) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                payload = line[6:]
                print(payload, flush=True)

if __name__ == "__main__":
    main()
```

Install it as a script entry point in your team's internal package, or just
`chmod +x` and drop it on `$PATH`.

## Trade-offs

| | |
|---|---|
| **Simple** | No persistent state needed client-side — fairy holds the session. |
| **Streamable** | SSE is plain text; the terminal gets output as it arrives. |
| **Re-entrant** | Save `session_id` and call `POST /sessions/{id}/prompt` to continue. |
| **Long prompts** | For large diffs or file contents, pipe via stdin and pass as the prompt string. |
| **Auth** | Store the token in `~/.config/mytool/token` or an env var — never hardcode it. |

For teams that need richer output formatting (spinners, syntax highlighting),
feed the SSE events through [Rich](https://github.com/Textualize/rich) before
printing.
