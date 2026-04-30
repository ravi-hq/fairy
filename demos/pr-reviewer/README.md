# PR Reviewer — Streaming AI Code Review Demo

A self-contained demo that shows how an AI agent can stream a thorough,
categorized code review in real time as you browse pull request diffs.

## What it does

- **Left panel** — three pre-loaded mock PRs (Python, TypeScript, Go) shown as
  cards with title, author, file stats, and a syntax-highlighted diff viewer.
- **Right panel** — as the mock agent runs, findings stream in organized under
  category headers (Security, Correctness, Performance, Style) with coloured
  severity badges (Critical / High / Medium / Low / Good). A summary card
  appears at the end.

## Planted bugs each PR catches

| PR | Bug |
|----|-----|
| Python auth middleware | SQL injection in `verify_token`, timing attack, in-process rate-limit state, dict race condition |
| React hooks dashboard | WebSocket never closed (memory leak), missing `userId` dependency, hardcoded `localhost` URL |
| Go concurrent worker | `ProcessAll` returns before goroutines finish (missing WaitGroup), SQL injection, connection pool leak |

## Quick start

```bash
cd demos/pr-reviewer
pip install -r requirements.txt
uvicorn app:app --reload
```

Open http://localhost:8000, select a PR card, and click **Review this PR**.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/reviews/prs` | JSON list of the three mock PRs |
| `GET` | `/reviews/stream/{pr_id}` | SSE stream of the review (`pr-1`, `pr-2`, `pr-3`) |

## File layout

```
demos/pr-reviewer/
  app.py           # FastAPI: PR listing + SSE review stream
  review_data.py   # Mock PR diffs, opening lines, findings, summaries
  static/
    index.html     # Split-panel UI (vanilla JS, no build step)
  requirements.txt
  README.md
```
