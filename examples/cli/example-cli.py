#!/usr/bin/env python3
"""example-cli.py — a minimal alias-ready CLI over Agent on Demand.

A drop-in replacement for `claude -p "<prompt>"` that runs the agent inside a
fresh Sprite sandbox with a pinned model, system prompt, tool set, and repo
list. Fork this file, edit the three config blocks below, drop it on your
PATH, and alias it.

    alias agent=/path/to/example-cli.py
    agent "work on the latest open issue in ravi-hq/fairy"

Requires: Python 3.11+, AOD_API_URL, AOD_API_TOKEN.
"""

from __future__ import annotations

import os
import sys

from aod_client import AodClient, AodError
from claude_format import ClaudeFormatter

# -------- configure me ------------------------------------------------------

AGENT = {
    "name": "example-cli",
    "model": "claude-sonnet-4-6",
    "runtime": "claude",
    "system": (
        "You are a senior engineer working inside a Sprite sandbox. "
        "Investigate thoroughly before editing. Keep changes minimal and "
        "focused on the task you were given."
    ),
    "mcp_servers": [
        {"name": "context7", "type": "url", "url": "https://mcp.context7.com/mcp"},
    ],
}

ENVIRONMENT = {
    "name": "example-cli",
    "packages": {},
    "networking": {"type": "unrestricted"},
}

REPOS = [
    "https://github.com/ravi-hq/fairy",
]

TIMEOUT = 1200

# ---------------------------------------------------------------------------


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        sys.exit(f"{name} must be set")
    return val


def _emit(formatted: str) -> None:
    print(formatted, flush=True)
    print(flush=True)


def _handle_stream(client: AodClient, session_id: str) -> int:
    formatter = ClaudeFormatter()
    for event in client.stream_session(session_id):
        kind = event.get("type")
        if kind == "output":
            data = event.get("data", "")
            if event.get("stream") == "stderr":
                sys.stderr.write(data)
                sys.stderr.flush()
                continue
            for line in formatter.feed(data):
                _emit(line)
        elif kind == "exit":
            for line in formatter.flush():
                _emit(line)
            return int(event.get("code") or 0)
        elif kind in ("error", "terminated", "stale"):
            print(f"\n[{kind}] {event.get('message', '')}", file=sys.stderr)
            return 1
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} '<prompt>'")
    prompt = " ".join(sys.argv[1:])

    client = AodClient(_env("AOD_API_URL", "http://localhost:8777"), _env("AOD_API_TOKEN"))

    try:
        env_id = client.ensure("environments", ENVIRONMENT["name"], ENVIRONMENT)
        agent_id = client.ensure("agents", AGENT["name"], {**AGENT, "environment_id": env_id})
        gh_token = os.environ.get("GITHUB_TOKEN")
        session = client.create_session(
            agent_id=agent_id,
            prompt=prompt,
            timeout=TIMEOUT,
            resources=[
                {
                    "type": "github_repository",
                    "url": url,
                    "authorization_token": gh_token,
                }
                for url in REPOS
            ],
        )
        print(f"# session {session['id']}", file=sys.stderr)
        return _handle_stream(client, session["id"])
    except AodError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
