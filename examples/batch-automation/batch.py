#!/usr/bin/env python3
"""batch.py — run one agent prompt per line against Agent on Demand, concurrently.

Reads prompts from a file (one per line) or stdin, creates a session per prompt
capped at AOD_MAX_CONCURRENT, collects each session's stdout via SSE, deletes
the session, and prints the results to stdout as `--- <prompt> ---\\n<output>`.

Usage:
    ./batch.py prompts.txt
    ./batch.py < prompts.txt
    printf 'summarize file a\\nsummarize file b\\n' | ./batch.py

Required env vars:
    AOD_API_URL, AOD_API_TOKEN    (read by aod.AsyncClient())
    AOD_AGENT_ID                   agent to run each prompt against
Optional:
    AOD_MAX_CONCURRENT   default 5 — cap in-flight sessions
    AOD_TIMEOUT          default 300 — per-session timeout (seconds)
    AOD_POLL_INTERVAL    default 3 — seconds between status polls
    AOD_POLL_ATTEMPTS    default 120 — max polls before giving up
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aod import AodError, AsyncClient, ConflictError

AGENT_ID = os.environ["AOD_AGENT_ID"]
MAX_CONCURRENT = int(os.environ.get("AOD_MAX_CONCURRENT", "5"))
TIMEOUT = int(os.environ.get("AOD_TIMEOUT", "300"))
POLL_INTERVAL = float(os.environ.get("AOD_POLL_INTERVAL", "3"))
POLL_ATTEMPTS = int(os.environ.get("AOD_POLL_ATTEMPTS", "120"))

_TERMINAL = {"completed", "failed", "terminated"}


async def _wait_terminal(client: AsyncClient, session_id: str) -> str:
    """Poll until the session reaches a terminal status or attempts run out."""
    for _ in range(POLL_ATTEMPTS):
        await asyncio.sleep(POLL_INTERVAL)
        session = await client.sessions.get(session_id)
        if session.status in _TERMINAL:
            return session.status
    # Caller treats "unknown" as non-fatal and still tries to drain the stream.
    return "unknown"


async def _drain_stdout(client: AsyncClient, session_id: str) -> str:
    parts: list[str] = []
    async with client.sessions.stream(session_id) as events:
        async for event in events:
            if event.type == "output" and event.extra.get("stream") == "stdout":
                parts.append(event.extra.get("data", ""))
            elif event.type in ("exit", "error", "terminated", "stale"):
                break
    return "".join(parts)


async def run_one(client: AsyncClient, prompt: str) -> str:
    ack = await client.sessions.create(agent_id=AGENT_ID, prompt=prompt, timeout=TIMEOUT)
    session_id = str(ack.id)
    try:
        await _wait_terminal(client, session_id)
        return await _drain_stdout(client, session_id)
    finally:
        try:
            await client.sessions.delete(session_id)
        except (ConflictError, AodError):
            # Already running/deleted, or transient — examples shouldn't
            # block cleanup on best-effort teardown.
            pass


async def batch(prompts: list[str]) -> list[str | BaseException]:
    sem = asyncio.Semaphore(MAX_CONCURRENT)
    async with AsyncClient() as client:

        async def bounded(prompt: str) -> str:
            async with sem:
                return await run_one(client, prompt)

        return await asyncio.gather(
            *(bounded(p) for p in prompts),
            return_exceptions=True,
        )


def _read_prompts(argv: list[str]) -> list[str]:
    if len(argv) > 1:
        text = Path(argv[1]).read_text()
    else:
        text = sys.stdin.read()
    return [line.strip() for line in text.splitlines() if line.strip()]


def main() -> int:
    prompts = _read_prompts(sys.argv)
    if not prompts:
        print("no prompts to run (pass a file or pipe via stdin)", file=sys.stderr)
        return 1
    print(f"# running {len(prompts)} prompts, max {MAX_CONCURRENT} concurrent", file=sys.stderr)

    results = asyncio.run(batch(prompts))

    failures = 0
    for prompt, result in zip(prompts, results):
        print(f"--- {prompt} ---")
        if isinstance(result, BaseException):
            failures += 1
            print(f"FAILED: {result!r}")
        else:
            print(result)
        print()

    if failures:
        print(f"# {failures}/{len(prompts)} prompts failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
