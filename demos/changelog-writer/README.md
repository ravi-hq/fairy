# Changelog Writer — Concurrent Agents Demo

Paste a git log, click **Write Changelog**, and watch three concurrent agent
sessions simultaneously produce a **CHANGELOG entry**, **blog post intro**,
and **tweet thread** — all streaming in parallel.

## What it demonstrates

The concurrent fan-out pattern: one input, three simultaneous agent sessions,
each producing a different output format, multiplexed onto a single SSE stream
and routed client-side to the correct panel.

## Run it

```bash
cd demos/changelog-writer
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open http://localhost:8000

## How it works

- `GET /changelog/releases` — returns the 3 pre-loaded sample releases
- `GET /changelog/stream/{release_id}` — multiplexed SSE endpoint that fans
  out 3 asyncio tasks concurrently; each event carries a `format` field
  (`changelog` | `blogpost` | `tweetthread`) so the client routes it to the
  correct panel

The three mock agent streams run at slightly different base speeds so all
three panels visibly fill simultaneously but feel independent.

## Sample releases

| ID | Description |
|----|-------------|
| `v2.4.0-cli` | Developer CLI — watch mode, parallel tests, perf wins |
| `v1.8.0-api` | SaaS API — webhooks, batch endpoint, breaking change |
| `v3.1.0-mobile` | Mobile app — offline mode, biometrics, bulk actions |
