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

### Workspace metrics bar

Pinned to the top of the page — `5 missions in flight · 32 tasks running
across 4 agents · $47.82 spent today · 11 awaiting humans`. A live read on
the whole agent workforce.

### Missions tab (default)

Three columns:

- **Left** — mission list with a "**+ New mission**" button, search box, and
  filter pills (All / In flight / Done / Blocked / Drafting). Each card has
  a status pill, an owner avatar, a progress bar (`4/6 tasks done`), and a
  last-activity timestamp. The left border is colored by status.
- **Center** — the selected mission's goal, owner, target date, and a
  **metrics strip** (Spent so far, Time invested, Tasks complete, ETA),
  followed by the outcome description and the **work tree** of nested task
  rows. Each row shows the assignee chip (purple for human, blue for agent,
  distinct icons), a status pill, and — for running agent tasks — a live
  "currently doing" sub-line. Rows expand to show streaming output, linked
  PRs, the linked brief, and a "**View session log**" link that opens a
  full terminal-style replay (see below).
- **Right** — an activity feed with a "**This mission / All missions**"
  toggle. Mission scope shows timestamped events for the selected mission;
  All-missions scope is a **firehose** of events across the whole workspace
  (last 24 h), updated live every ~8 seconds.

For Mission #1 ("Ship v2.4 of the checkout flow"), Scout's regression-suite
task is **streaming live** — every 1.5 s a new test result line appears.

### Plan-a-mission wizard

The "**+ New mission**" button opens a 4-step modal:

1. **Outcome** — a textarea plus four example chips (incident / research /
   ship / onboard) that pre-fill the textarea.
2. **Proposed plan** — a 1.5 s thinking loader, then the wizard
   keyword-matches your outcome to one of seven hardcoded templates (or
   falls back to generic) and proposes an editable work tree. Each row is a
   title input, an assignee dropdown (5 agents + 3 humans), an estimated
   duration, a remove button, and a "why this task" subline. There's a
   `+ Add task` row at the bottom.
3. **Owner & target** — pick from Maya / Jake / Sam, free-text target date.
4. **Review & launch** — summary card. Clicking *Launch mission* posts to
   `POST /api/missions`; the new row appears in the list with status
   `in-flight`, the first task `running`, the rest `queued`. An async loop
   then advances three more tasks (queued → running → done) at ~6 s
   intervals so the demo feels alive.

### Templates tab

Grid of 6 mission templates: Investigate Incident, Research Competitor,
Ship Feature, Onboard New Hire, Migrate Database, Quarterly Plan. Each
card shows its icon, name, description, task count, and a "Used N times"
stat. Clicking *Use template* opens the wizard pre-loaded at Step 2.

### Session-log replay modal

Clicking "View session log" on any task opens a terminal-style modal with:

- A meta strip (assignee, duration, cost, tokens, status pill).
- A scrollable monospace pane that **streams** the log lines progressively
  (default 2× speed; control offers 1× / 2× / 4× / Skip). Lines are
  syntax-colored by prefix: `[setup]`, `[agent]`, `[think]`, `[tool]`,
  `[output]`, `[result]`.
- A right sidebar with section navigation (Setup / Tools / Output / Result).
- For human-assigned tasks, a different layout: a review panel with sample
  comments and visual-only Approve / Request Changes buttons.

### Knowledge Base tab

Two columns:

- **Left** — auto-written docs: post-mortems, decision logs, weekly digests,
  spec drafts. Each card has an icon by category, a title, and a sub-line.
- **Right** — the selected doc, rendered as Markdown, with a front-matter
  panel showing the source mission and an **"Open source mission #N"** link
  that flips back to the Missions tab and selects the originating mission.

### Assignee profile popover

Clicking any assignee chip on a task opens a small popover. For agents:
name, model, recent runs, success rate, and a *Open in Forge* link. For
humans: name, role, current load.

## Pre-loaded missions

| # | Title | Status | Tasks |
|---|---|---|---|
| 1 | Ship v2.4 of the checkout flow | in-flight | 6 (4 done, 1 running, 1 blocked) |
| 2 | Investigate weekend latency spike | done | 4 (all complete) |
| 3 | Decide on payment provider for EU rollout | in-flight | 5 (3 done, 2 awaiting human) |
| 4 | Onboard the new platform-eng team | drafting | 2 (queued) |
| 5 | Migrate user notifications to v3 schema | in-flight | 7 (4 done, 1 awaiting, 2 queued) — Migrator-agent thread |
| 6 | Q2 capacity planning | drafting | 4 (queued) |

## Pre-loaded knowledge-base docs

1. **Postmortem: Weekend latency spike (2026-04-26)** — generated from
   Mission #2.
2. **Decision Log: EU payment provider** — generated from Mission #3.
3. **Weekly Engineering Digest (week of 2026-04-22)** — auto-aggregated
   across all missions.
4. **Spec: retry-with-backoff for `/api/checkout`** — generated from
   Mission #1's Spec Drafter task.

## The "self-writing knowledge base" idea

Every mission produces a session log: provisioning events, agent outputs,
human approvals, artifact links. The post-mortem doc on the Knowledge Base
tab was *not* hand-written — it was distilled from those 14 session events
into a structured incident report. Confluence asks humans to remember to
write the post-mortem; Orbit treats the post-mortem as a deliverable of
the mission itself.

## Architecture

```
app.py            FastAPI app — missions + docs + templates + session
                  logs + firehose + profiles + workspace metrics. SSE for
                  the regression-suite live line and the firehose.
scenarios.py      Pre-loaded missions, tasks, KB docs, mission templates,
                  per-task session logs, firehose seed, agent/human
                  profiles, workspace metrics. In-memory dataclasses + dicts.
static/
  index.html      Single-file UI: vanilla JS, inline CSS, inline SVG icons,
                  a tiny markdown renderer for KB docs, plus the wizard,
                  session-log replay, popovers, and templates gallery.
requirements.txt  fastapi, uvicorn, sse-starlette
```

No database. No build step. No external CDN. Created missions live in
process memory (lost on restart).

## Not real AoD

This demo does **not** call Agent on Demand or any other backend. The
"agent sessions" are hardcoded `scenarios.py` data; the live regression
stream and firehose ticker are timer-driven cycles. The point is to show
what the *product* looks like — not to actually run the agents.
