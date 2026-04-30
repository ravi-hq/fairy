# Orbit — agent-native Atlassian

Orbit is a demo / POC for what **Jira + Confluence look like when half the
work is done by agents**.

In today's Jira, the atomic unit is a *ticket assigned to a human*. The board
shows what the human said the work was the last time they touched it.

In Orbit, the atomic unit is a **mission** — an outcome — that the system
decomposes into a tree of work items and dispatches to agents, humans, or
both. The board reflects the *actual* state of work: when an agent finishes,
the next task starts; when a human review is pending, the status reflects it.
Confluence becomes a knowledge base that **writes itself** from completed
missions: post-mortems, decision logs, and weekly summaries are
auto-generated from session logs.

## How to run

```bash
cd demos/orbit
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open <http://localhost:8000>.

## What you'll see

### Missions tab (default)

Three columns:

- **Left** — mission list. Each card has a status pill (`in-flight`, `done`,
  `blocked`, `drafting`), an owner avatar, a progress bar (`4/6 tasks done`),
  and a last-activity timestamp. The left border is colored by status.
- **Center** — the selected mission's goal, owner, target date, outcome
  description, and a **work tree** of nested task rows. Each row shows the
  assignee chip (purple for human, blue for agent, distinct icons), a status
  pill, and — for running agent tasks — a live "currently doing" sub-line.
  Rows expand to show streaming output, linked PRs, or the linked brief.
- **Right** — the activity feed for the selected mission: short timestamped
  events ("Researcher finished competitive scan · 4m ago", "@maya approved
  PR #1234 · 1h ago").

For Mission #1 ("Ship v2.4 of the checkout flow"), Scout's regression-suite
task is **streaming live** — every 1.5 s a new test result line appears
(occasionally a failure). The snapshot still freezes at 41/120 — the lines
just cycle so the demo always shows motion.

### Knowledge Base tab

Two columns:

- **Left** — auto-written docs: post-mortems, decision logs, weekly digests,
  spec drafts. Each card has an icon by category, a title, and a sub-line
  saying "auto-written from session log · 2h ago".
- **Right** — the selected doc, rendered as Markdown, with a front-matter
  panel showing the source ("Generated from mission #2 — Investigate weekend
  latency spike. 14 session events distilled.").

## Pre-loaded missions

| # | Title | Status | Tasks |
|---|---|---|---|
| 1 | Ship v2.4 of the checkout flow | in-flight | 6 (4 done, 1 running, 1 blocked) |
| 2 | Investigate weekend latency spike | done | 4 (all complete) |
| 3 | Decide on payment provider for EU rollout | in-flight | 5 (3 done, 2 awaiting human) |
| 4 | Onboard the new platform-eng team | drafting | 2 (queued) |

## Pre-loaded knowledge-base docs

1. **Postmortem: Weekend latency spike (2026-04-26)** — generated from
   Mission #2. Real engineer-flavored post-mortem with timeline, root cause,
   contributing factors, and tracked action items.
2. **Decision Log: EU payment provider** — generated from Mission #3.
   Options table, recommendation (Adyen at $1.98M/yr advantage), open
   questions.
3. **Weekly Engineering Digest (week of 2026-04-22)** — auto-aggregated
   across all missions: shipped, in-flight, blocked, new decisions, top
   agent runs.
4. **Spec: retry-with-backoff for `/api/checkout`** — generated from
   Mission #1's Spec Drafter task. Standard engineering spec sections.

## The "self-writing knowledge base" idea

Every mission produces a session log: provisioning events, agent outputs,
human approvals, artifact links. The post-mortem doc on the Knowledge Base
tab was *not* hand-written — it was distilled by a Spec-Drafter-style agent
from those 14 session events into a structured incident report. The same is
true of the decision log (9 events) and the weekly digest (47 events across
4 missions).

Confluence asks humans to remember to write the post-mortem; Orbit treats
the post-mortem as a deliverable of the mission itself.

## Architecture

```
app.py            FastAPI app — missions + docs endpoints, SSE for the
                  one running agent task (Mission #1, Scout regression).
scenarios.py      All pre-loaded missions, tasks, activity, and KB doc
                  bodies. In-memory dataclasses.
static/
  index.html      Single-file UI: vanilla JS, inline CSS, inline SVG icons,
                  a tiny markdown renderer for KB docs.
requirements.txt  fastapi, uvicorn, sse-starlette
```

No database. No build step. No external CDN.

## Not real AoD

This demo does **not** call Agent on Demand or any other backend. The "agent
sessions" are hardcoded `scenarios.py` data. The live regression-suite stream
is a 10-line cycle on a 1.5 s timer in `app.py`. The point is to show what
the *product* looks like — not to actually run the agents.
