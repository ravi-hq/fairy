# Pattern: CLI Wrapper

Your team needs a one-liner that kicks off an AI coding task without opening a
browser or writing HTTP boilerplate.

Python examples use the official [`aod-sdk`](../sdks/python.md) package
(`pip install aod-sdk`).

## Shape of the solution

Wrap Agent on Demand in a thin CLI that creates a session via `client.sessions.create(...)`, then opens
the SSE stream with `client.sessions.stream(session_id)` and prints each event to the
terminal. The user gets a familiar `mytool code "<prompt>"` interface backed by
a real agent session.

Because Agent on Demand tracks session state, the CLI can also support follow-up prompts
via `client.sessions.prompt(session_id, ...)` — but the simplest starting point is
single-shot: create → stream → exit.

## Example

```python
#!/usr/bin/env python3
"""mytool code <prompt> — run an Agent on Demand session and stream output."""
import os
import sys

from aod import Client

AGENT_ID = os.environ["AOD_AGENT_ID"]  # your team's shared agent

def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mytool code '<prompt>'", file=sys.stderr)
        return 1

    prompt = " ".join(sys.argv[1:])

    with Client() as client:  # reads AOD_API_URL + AOD_API_TOKEN
        ack = client.sessions.create(agent_id=AGENT_ID, prompt=prompt)
        print(f"# session {ack.id}", file=sys.stderr)

        with client.sessions.stream(ack.id) as events:
            for event in events:
                if event.type == "output":
                    print(event.extra["data"], end="", flush=True)
                elif event.type == "exit":
                    return int(event.extra.get("code") or 0)
                elif event.type in ("error", "terminated", "stale"):
                    print(f"\n[{event.type}]", file=sys.stderr)
                    return 1
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

Install it as a script entry point in your team's internal package, or just
`chmod +x` and drop it on `$PATH`.

A more complete reference — with a spinner, Claude stream-json formatting, and
GitHub repo cloning — lives at
[`examples/cli/`](https://github.com/ravi-hq/agent-on-demand/tree/main/examples/cli)
in the repo.

## Trade-offs

| | |
|---|---|
| **Simple** | No persistent state needed client-side — Agent on Demand holds the session. |
| **Streamable** | `client.sessions.stream()` yields typed events as they arrive. |
| **Re-entrant** | Save `session_id` and call `client.sessions.prompt(id, prompt=...)` to continue. |
| **Long prompts** | For large diffs or file contents, pipe via stdin and pass as the prompt string. |
| **Auth** | Set `AOD_API_TOKEN` (or `~/.config/mytool/token`) — never hardcode it in source. |

For richer output formatting, feed events through
[`aod.pretty.claude.ClaudeFormatter`](../sdks/python.md#pretty-printing-claude-output)
(for Claude runtimes) or [Rich](https://github.com/Textualize/rich) before
printing.
