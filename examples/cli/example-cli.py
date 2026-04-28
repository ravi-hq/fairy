#!/usr/bin/env python3
"""example-cli.py — a minimal alias-ready CLI over Agent on Demand.

A drop-in replacement for `claude -p "<prompt>"` that runs the agent inside a
fresh Sprite sandbox with a pinned model, system prompt, tool set, and repo
list. Fork this file, edit the three config blocks below, drop it on your
PATH, and alias it.

    alias agent=/path/to/example-cli.py
    agent "work on the latest open issue in ravi-hq/fairy"
    agent --session <uuid> "now open a PR with the fix"

Requires: Python 3.11+, `pip install aod-sdk`, AOD_API_URL, AOD_API_TOKEN.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Any

from aod import AodError, Client, SessionAck, StreamEvent
from aod.pretty.claude import ClaudeFormatter

# -------- configure me ------------------------------------------------------

AGENT: dict[str, Any] = {
    "name": "example-cli",
    "model": "anthropic/claude-sonnet-4-6",
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

ENVIRONMENT: dict[str, Any] = {
    "name": "example-cli",
    "packages": {},
    "networking": {"type": "unrestricted"},
}

REPOS = [
    "https://github.com/ravi-hq/fairy",
]

TIMEOUT = 1200

# Human-readable labels for the `stage` SSE events AoD emits during session
# provisioning. See site/docs/api/streaming.md for the full list of stage
# names. Anything missing here falls through to the raw stage identifier.
STAGE_LABELS = {
    "create_sprite": "creating sandbox",
    "network_policy": "applying network policy",
    "env_file": "writing env file",
    "git_credentials": "writing git credentials",
    "provision_setup": "installing packages, cloning repos, running setup",
    "mcp_config": "writing mcp config",
    "skills": "writing skills",
    "runtime_start": "starting agent",
}


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


# ---------------------------------------------------------------------------


def _emit(formatted: str) -> None:
    print(formatted, flush=True)
    print(flush=True)


class _Spinner:
    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, message: str) -> None:
        self._message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0

    def start(self) -> None:
        if self._thread or not sys.stderr.isatty():
            return
        # Clear any stop flag left over from a previous stop(): without this
        # the new worker sees _stop already set and exits on its first tick.
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._thread.join(timeout=0.5)
        sys.stderr.write("\r\x1b[K")
        sys.stderr.flush()
        self._thread = None

    def set_message(self, message: str) -> None:
        # String assignment is atomic in CPython; no lock needed for the
        # spinner thread to pick up the new value on its next tick.
        self._message = message

    def _run(self) -> None:
        i = 0
        while not self._stop.wait(0.1):
            elapsed = int(time.monotonic() - self._started_at)
            frame = self._FRAMES[i % len(self._FRAMES)]
            sys.stderr.write(f"\r{frame} {self._message} · {elapsed}s")
            sys.stderr.flush()
            i += 1


def _handle_stage(event: StreamEvent, spinner: _Spinner, idle_message: str) -> None:
    stage = event.extra.get("stage", "")
    state = event.extra.get("state", "")
    label = _stage_label(stage)
    if state == "started":
        spinner.set_message(label)
        return
    secs = (event.extra.get("duration_ms") or 0) / 1000
    spinner.stop()
    if state == "done":
        print(f"✓ {label} · {secs:.1f}s", file=sys.stderr)
        spinner.set_message(idle_message)
        spinner.start()
    elif state == "failed":
        msg = event.extra.get("message") or ""
        line = f"✗ {label} failed · {secs:.1f}s"
        if msg:
            line += f": {msg}"
        print(line, file=sys.stderr)
        # No restart — a session-level error/terminated event is coming.


def _handle_stream(client: Client, session_id: str, waiting_for: str) -> int:
    formatter = ClaudeFormatter()
    spinner = _Spinner(waiting_for)
    spinner.start()
    try:
        with client.sessions.stream(session_id) as events:
            for event in events:
                if event.type == "stage":
                    _handle_stage(event, spinner, waiting_for)
                elif event.type == "output":
                    if event.extra.get("stream") == "stderr":
                        spinner.stop()
                        sys.stderr.write(event.extra.get("data", ""))
                        sys.stderr.flush()
                        continue
                    for line in formatter.consume(event):
                        spinner.stop()
                        _emit(line)
                elif event.type == "exit":
                    spinner.stop()
                    for line in formatter.flush():
                        _emit(line)
                    return int(event.extra.get("code") or 0)
                elif event.type in ("error", "terminated", "stale"):
                    spinner.stop()
                    print(
                        f"\n[{event.type}] {event.extra.get('message', '')}",
                        file=sys.stderr,
                    )
                    return 1
        return 1
    finally:
        spinner.stop()


def _ensure_environment(client: Client) -> str:
    """Return the id of the configured environment, creating it if missing."""
    for env in client.environments.list():
        if env.name == ENVIRONMENT["name"]:
            return str(env.id)
    return str(client.environments.create(**ENVIRONMENT).id)


def _ensure_agent(client: Client, environment_id: str) -> str:
    for agent in client.agents.list():
        if agent.name == AGENT["name"]:
            return str(agent.id)
    return str(client.agents.create(**AGENT, environment_id=environment_id).id)


def _new_session(client: Client, prompt: str) -> SessionAck:
    env_id = _ensure_environment(client)
    agent_id = _ensure_agent(client, env_id)
    gh_token = os.environ.get("GITHUB_TOKEN")
    return client.sessions.create(
        agent_id=agent_id,
        prompt=prompt,
        timeout=TIMEOUT,
        resources=[
            {"type": "github_repository", "url": url, "authorization_token": gh_token}
            for url in REPOS
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        usage="%(prog)s [--session <id>] '<prompt>'",
    )
    parser.add_argument(
        "--session",
        metavar="ID",
        help="Continue an existing session instead of creating a new one.",
    )
    parser.add_argument("prompt", nargs="+", help="Prompt to send to the agent.")
    args = parser.parse_args()
    prompt = " ".join(args.prompt)

    # Client() reads AOD_API_URL and AOD_API_TOKEN from the environment.
    try:
        client = Client()
    except ValueError as e:
        sys.exit(str(e))

    try:
        with client:
            if args.session:
                ack = client.sessions.prompt(args.session, prompt=prompt, timeout=TIMEOUT)
                print(f"# session {ack.id} (turn {ack.current_turn})", file=sys.stderr)
                waiting_for = "resuming session"
                session_id = args.session
            else:
                ack = _new_session(client, prompt)
                print(f"# session {ack.id}", file=sys.stderr)
                waiting_for = "preparing sandbox"
                session_id = str(ack.id)
            return _handle_stream(client, session_id, waiting_for)
    except AodError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
