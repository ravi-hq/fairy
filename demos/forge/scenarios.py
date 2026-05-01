"""
Hardcoded library agents for the Forge demo.

Each entry is a self-contained agent definition: a system prompt, a tool list,
a "done when" criterion, surface metrics (runs, rating, forks), version
history, and a per-agent `mock_test_run` — a sequence of (delay_sec, kind, text)
tuples that the SSE stream replays. Kinds are: "stage", "tool", "thought",
"output", "result", "exit".

No real Agent on Demand calls happen here. Everything is a fixture.
"""

from __future__ import annotations

import json
from typing import Any

# --- Owners ------------------------------------------------------------------

OWNERS: dict[str, dict[str, str]] = {
    "maya": {"name": "Maya M.", "initials": "MM", "color": "#d2a8ff"},
    "jake": {"name": "Jake G.", "initials": "JG", "color": "#7ee787"},
    "sam":  {"name": "Sam P.",  "initials": "SP", "color": "#ffa657"},
}


# --- Helpers ----------------------------------------------------------------

def _result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2)


# --- Per-agent test runs -----------------------------------------------------

COMPETITIVE_RESEARCHER_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-7d2a in 0.18s"),
    (0.3, "stage",   "provision_setup       installed 14 tools in 1.2s"),
    (0.3, "stage",   "runtime_start         claude-sonnet ready"),
    (0.5, "thought", "Plan: pull last 14 days of releases for Linear, Asana, Height; cross-check pricing pages."),
    (0.7, "tool",    "web_search            'Linear changelog April 2026'"),
    (0.6, "output",  "Found 3 dated entries on linear.app/changelog (Apr 2, Apr 16, Apr 24)."),
    (0.8, "tool",    "web_fetch             https://linear.app/changelog"),
    (0.7, "output",  "Linear shipped 'Initiatives 2.0' on Apr 24 — replaces Roadmaps for paid plans."),
    (0.9, "tool",    "web_fetch             https://asana.com/pricing"),
    (0.6, "output",  "Asana raised Business tier $24.99 -> $30.49 / seat / mo (Apr 18)."),
    (0.7, "tool",    "web_fetch             https://height.app/blog"),
    (0.6, "output",  "Height paused public roadmap; last post Mar 9. Likely repositioning."),
    (0.5, "thought", "Synthesize positioning shifts. Linear is moving up-market; Asana raising prices; Height quiet."),
    (1.0, "result",  _result({
        "summary": "Linear and Asana are pushing up-market this month; Height has gone quiet.",
        "competitors": [
            {"name": "Linear",  "shift": "Initiatives 2.0 — replaces Roadmaps on paid tiers", "date": "2026-04-24"},
            {"name": "Asana",   "shift": "Business tier price up 22% to $30.49/seat/mo",       "date": "2026-04-18"},
            {"name": "Height",  "shift": "Paused public roadmap, no posts since 2026-03-09",   "date": "2026-03-09"},
        ],
        "pricing_changes": [
            {"vendor": "Asana",  "from": "$24.99", "to": "$30.49", "tier": "Business"},
        ],
        "recommendation": "Counter Linear's Initiatives push with our cross-team rollups demo before May 5.",
    })),
    (0.2, "exit", "0"),
]


PR_REVIEWER_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-9f3b in 0.14s"),
    (0.3, "stage",   "provision_setup       cloned ravi-hq/fairy@feat/session-interrupt"),
    (0.3, "stage",   "runtime_start         claude-opus ready"),
    (0.5, "thought", "Reviewing PR #331: 'Add /interrupt endpoint'. 7 files, +312 / -18."),
    (0.6, "tool",    "filesystem.read       src/agent_on_demand/views/sessions.py"),
    (0.6, "tool",    "filesystem.read       src/agent_on_demand/session_state.py"),
    (0.7, "tool",    "git.diff              main..HEAD -- src/"),
    (0.5, "thought", "interrupt() bypasses select_for_update — concurrent /prompt could race."),
    (0.6, "tool",    "bash                  rg 'select_for_update' src/agent_on_demand/views/"),
    (0.5, "output",  "Confirmed: /prompt and /terminate use select_for_update; /interrupt does not."),
    (0.7, "tool",    "bash                  pytest tests/test_sessions.py -k interrupt -q"),
    (0.5, "output",  "12 passed in 1.42s — but no concurrent-prompt test exists."),
    (0.5, "thought", "Categorize findings: 1 bug, 2 style, 1 security-adjacent, 1 perf."),
    (1.0, "result",  _result({
        "pr": "#331",
        "verdict": "request-changes",
        "categories": {
            "bugs": [
                "interrupt_session() reads session.state without select_for_update — concurrent POST /prompt can flip state to running between the read and the write. Match the lock pattern in terminate_session().",
            ],
            "style": [
                "session_state.py:42 — `is_interruptible` should mirror the `can_*` naming used elsewhere (rename to `can_interrupt`).",
                "views/sessions.py:118 — drop the inline f-string; use `logger.info('interrupted', extra={...})` for structured log compatibility.",
            ],
            "security": [
                "Audit log entry omits the actor (request.user.id). Add it — required for the SOC2 access review trail.",
            ],
            "performance": [
                "interrupt() refetches the Session row twice. One `select_for_update()` would cover both reads.",
            ],
        },
        "must_fix_before_merge": ["bugs[0]", "security[0]"],
    })),
    (0.2, "exit", "0"),
]


ON_CALL_SCOUT_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-1a8c in 0.16s"),
    (0.3, "stage",   "provision_setup       loaded honeycomb + github + slack creds from env"),
    (0.3, "stage",   "runtime_start         claude-sonnet ready"),
    (0.5, "thought", "Page received: 'p99 latency on /api/sessions > 4s for 8 minutes.' Run the playbook."),
    (0.7, "tool",    "honeycomb.query       dataset=fairy-prod, calc=HEATMAP(duration_ms), filter=route=/api/sessions, last=30m"),
    (0.6, "output",  "Hot band at 4.2s starts at 14:31 UTC. Pre-incident p99 was 380ms."),
    (0.7, "tool",    "honeycomb.bubbleup    pivot on db.statement"),
    (0.6, "output",  "Top contributor: SELECT ... FROM agent_session WHERE state = 'running' (table scan, no index)."),
    (0.7, "tool",    "github.search        repo:ravi-hq/fairy 'agent_session_state' merged:>2026-04-25"),
    (0.6, "output",  "PR #324 (merged 14:22 UTC) added a `state` filter to the listing endpoint. No migration."),
    (0.5, "thought", "Likely cause: PR #324 introduced a filter on an unindexed column 9 minutes before the latency spike. High confidence."),
    (0.6, "tool",    "slack.post           channel=#on-call, type=incident_brief"),
    (1.0, "result",  _result({
        "incident_id": "INC-2026-04-29-01",
        "hypothesis": "PR #324 added WHERE state='running' on agent_session without an index; query planner switched to a seq scan once the table heated up.",
        "confidence": "high",
        "evidence": [
            "Latency spike at 14:31 UTC; PR #324 merged at 14:22 UTC.",
            "BubbleUp pivot identifies the agent_session listing query as the top contributor.",
            "No migration accompanies PR #324.",
        ],
        "suggested_action": "Revert PR #324 OR add a partial index on agent_session(state) WHERE state='running'.",
        "page_human": False,
        "filed_in": "#on-call",
    })),
    (0.2, "exit", "0"),
]


CHANGELOG_WRITER_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-44e1 in 0.17s"),
    (0.3, "stage",   "provision_setup       checked out ravi-hq/fairy@v1.4.0..v1.5.0"),
    (0.3, "stage",   "runtime_start         claude-sonnet ready"),
    (0.5, "thought", "Range v1.4.0..v1.5.0 covers 47 commits. Group by user-visible vs internal."),
    (0.7, "tool",    "git.log               v1.4.0..v1.5.0 --pretty=format:'%h %s'"),
    (0.6, "output",  "47 commits found. 12 user-visible, 28 internal, 7 docs-only."),
    (0.6, "tool",    "filesystem.read       CHANGELOG.md (last release entry)"),
    (0.5, "thought", "Match prior voice: short, customer-friendly, no commit hashes inline."),
    (0.7, "tool",    "filesystem.write      docs/changelog/v1.5.0.md (customer-facing)"),
    (0.7, "tool",    "filesystem.write      docs/changelog/v1.5.0-internal.md (engineering)"),
    (0.7, "tool",    "filesystem.write      CHANGELOG.md (top-of-file entry, conventional)"),
    (0.5, "output",  "3 files written. Total 4.1 KB across all formats."),
    (1.0, "result",  _result({
        "release": "v1.5.0",
        "files_written": [
            {
                "path": "docs/changelog/v1.5.0.md",
                "format": "customer-facing markdown",
                "preview": "# What's new in v1.5.0\n\n**Stop a turn without losing the session.** The new POST /sessions/{id}/interrupt lets you cancel a running turn while keeping the underlying Sprite alive — perfect for chat UIs where the user clicks 'stop'.\n\n**Faster session cold-starts.** Provisioning is 38% faster on average...",
            },
            {
                "path": "docs/changelog/v1.5.0-internal.md",
                "format": "engineering log",
                "preview": "## v1.5.0 — engineering notes\n\n- POST /sessions/{id}/interrupt (#328) — uses select_for_update + procrastinate task cancellation\n- session_service refactor wraps up (#310, #305, #317)\n- mutation-test gate now blocks PRs (#296)\n...",
            },
            {
                "path": "CHANGELOG.md",
                "format": "conventional / Keep-a-Changelog",
                "preview": "## [1.5.0] - 2026-04-29\n\n### Added\n- `POST /sessions/{id}/interrupt` endpoint\n\n### Fixed\n- TypeScript SDK abort listener leak\n- Stream cancellation when iterator ends early\n\n### Changed\n- session_service extracted into per-stage modules\n",
            },
        ],
    })),
    (0.2, "exit", "0"),
]


SPEC_DRAFTER_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-2c5e in 0.15s"),
    (0.3, "stage",   "provision_setup       loaded linear + notion creds"),
    (0.3, "stage",   "runtime_start         claude-opus ready"),
    (0.5, "thought", "Prompt: 'Spec out a per-org rate limit on POST /sessions.' Linked: ENG-412."),
    (0.7, "tool",    "linear.fetch         ENG-412"),
    (0.6, "output",  "ENG-412: 'Some orgs are creating 200+ sessions/min during burst tests. We need a sane default.'"),
    (0.7, "tool",    "web_fetch            https://docs.aod.ravi.id/api/sessions"),
    (0.6, "output",  "Confirmed current behavior: no per-org limit; only a global concurrency cap of 200."),
    (0.7, "tool",    "notion.search        'rate limit' database=engineering-specs"),
    (0.6, "output",  "2 prior specs follow the 7-section template: Background / Goals / Non-goals / Design / Risks / Rollout / Open Questions."),
    (0.5, "thought", "Default limit: 60 req/min/org. Headers: X-RateLimit-{Limit,Remaining,Reset}. Per-org override in metadata."),
    (0.7, "tool",    "notion.create_page   db=engineering-specs, title='Spec: Per-org rate limit on POST /sessions'"),
    (1.0, "result",  _result({
        "spec_url": "notion.so/ravi/spec-rate-limit-sessions",
        "sections_present": [
            "Background", "Goals", "Non-goals", "Design",
            "Risks", "Rollout", "Open Questions",
        ],
        "default_limit": "60 req/min/org",
        "headers_added": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
        "linked_tickets": ["ENG-412"],
        "open_questions": [
            "Should burst credits accumulate (token bucket) or hard-reset (fixed window)?",
            "Do we expose per-org override via PUT /orgs/{id} or only via internal admin?",
        ],
    })),
    (0.2, "exit", "0"),
]


ONBOARDING_BUDDY_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-3e91 in 0.15s"),
    (0.3, "stage",   "provision_setup       loaded slack + notion + gcal creds"),
    (0.3, "stage",   "runtime_start         claude-sonnet ready"),
    (0.5, "thought", "New hire: Priya R. starts Mon. Role: Eng. Manager: Jake G. Build week 1 plan."),
    (0.7, "tool",    "notion.search        'onboarding template' database=people-ops"),
    (0.6, "output",  "Found 'Eng onboarding v4' — covers laptop, repos, on-call shadowing, week-1 1:1s."),
    (0.7, "tool",    "slack.lookup_user    name='Priya R.'"),
    (0.6, "output",  "Resolved @priya.r — joined Slack 2026-04-28, no DMs yet."),
    (0.7, "tool",    "gcal.find_freebusy   attendees=[priya, jake, maya, sam], window=next_5_days, dur=30m"),
    (0.6, "output",  "3 viable slots for each intro — picked least-disruptive across calendars."),
    (0.7, "tool",    "gcal.create_event    title='Welcome 1:1 — Priya x Jake', when=Mon 10:00"),
    (0.7, "tool",    "gcal.create_event    title='Codebase tour — Priya x Maya', when=Tue 14:00"),
    (0.7, "tool",    "gcal.create_event    title='Coffee — Priya x Sam', when=Wed 09:30"),
    (0.5, "thought", "Send the welcome DM with the schedule + repo links + on-call rotation map."),
    (0.7, "tool",    "slack.dm             to=@priya.r, type=welcome_packet"),
    (1.0, "result",  _result({
        "new_hire": "Priya R.",
        "role": "Engineering",
        "week_1_events_scheduled": 3,
        "intros_booked_with": ["Jake G.", "Maya M.", "Sam P."],
        "slack_dm_sent": True,
        "next_steps": [
            "Day 2: laptop pickup reminder (auto-fires Mon 09:00)",
            "Day 4: 'how's it going?' check-in DM",
            "Day 7: schedule second-week deep-dive sessions",
        ],
        "doc_link": "notion.so/ravi/priya-r-onboarding",
    })),
    (0.2, "exit", "0"),
]


INCIDENT_COMMS_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-8ac2 in 0.16s"),
    (0.3, "stage",   "provision_setup       loaded statuspage + slack creds"),
    (0.3, "stage",   "runtime_start         claude-opus ready"),
    (0.5, "thought", "Incident INC-2026-04-29-01 active. Sev2. Customer-visible: yes. Need first update within 10 min."),
    (0.7, "tool",    "slack.read           channel=#inc-2026-04-29-01, last=20"),
    (0.6, "output",  "Engineering hypothesis: PR #324 caused seq-scan on agent_session.state. Mitigation in flight."),
    (0.5, "thought", "Customer audience: API consumers. Tone: factual, no internal blame, no 'we apologize for the inconvenience' filler."),
    (0.7, "tool",    "statuspage.create_incident  name='Elevated latency on /api/sessions', impact=minor"),
    (0.6, "output",  "Statuspage incident st-9f4e2 created. Public URL: status.aod.ravi.id/incidents/9f4e2."),
    (0.7, "tool",    "statuspage.post_update      incident=st-9f4e2, status=identified"),
    (0.5, "thought", "Drop a parallel update in #customers-vip — these orgs hit Slack-paged tiers."),
    (0.7, "tool",    "slack.post                  channel=#customers-vip, type=incident_first_update"),
    (1.0, "result",  _result({
        "incident_id": "st-9f4e2",
        "severity": "minor",
        "status": "identified",
        "first_update_eta_min": 6.5,
        "channels_posted": ["statuspage", "#customers-vip"],
        "first_update_text": (
            "We're investigating elevated latency on POST /api/sessions starting at 14:31 UTC. "
            "Affected: ~3% of requests, p99 +3s above baseline. "
            "Root cause identified; mitigation in progress. Next update by 14:55 UTC."
        ),
        "next_update_due": "2026-04-29T14:55:00Z",
        "internal_runbook": "docs/runbook.md#customer-comms",
    })),
    (0.2, "exit", "0"),
]


DEPENDENCY_BUMPER_RUN: list[tuple[float, str, str]] = [
    (0.3, "stage",   "create_sprite          claimed sprite-6b22 in 0.16s"),
    (0.3, "stage",   "provision_setup       cloned ravi-hq/fairy@main"),
    (0.3, "stage",   "runtime_start         claude-sonnet ready"),
    (0.5, "thought", "Bump target: django 5.0.4 -> 5.1.2. Read CHANGELOG for breaking changes first."),
    (0.7, "tool",    "filesystem.read       pyproject.toml"),
    (0.6, "tool",    "bash                  uv pip install 'django==5.1.2' --upgrade"),
    (0.6, "output",  "Resolved 5.1.2; 4 transitive deps updated."),
    (0.7, "tool",    "filesystem.write      pyproject.toml (django==5.1.2)"),
    (0.7, "tool",    "filesystem.write      uv.lock"),
    (0.7, "tool",    "bash                  make test"),
    (1.2, "output",  "ran 412 tests in 38.4s — 412 passed, 0 failed."),
    (0.7, "tool",    "bash                  make lint"),
    (0.5, "output",  "ruff check passed."),
    (0.7, "tool",    "git.commit           -am 'chore(deps): bump django 5.0.4 -> 5.1.2'"),
    (0.7, "tool",    "git.push             origin chore/bump-django-5.1.2"),
    (0.7, "tool",    "github.pr_create     title='chore(deps): bump django 5.0.4 -> 5.1.2'"),
    (1.0, "result",  _result({
        "outcome": "pr_opened",
        "pr_url": "https://github.com/ravi-hq/fairy/pull/342",
        "from_version": "5.0.4",
        "to_version": "5.1.2",
        "tests": {"total": 412, "passed": 412, "failed": 0, "duration_sec": 38.4},
        "lint": "passed",
        "transitive_updates": ["asgiref", "sqlparse", "tzdata", "psycopg"],
        "notes": "No breaking changes hit. Safe to merge after CI confirms.",
    })),
    (0.2, "exit", "0"),
]


# --- Library -----------------------------------------------------------------

AGENTS: dict[str, dict[str, Any]] = {
    "competitive-researcher": {
        "id": "competitive-researcher",
        "name": "competitive-researcher",
        "description": "Researches competitor launches, summarizes positioning shifts, extracts pricing changes.",
        "version": 3,
        "category": "Research",
        "created_at": "2026-03-12",
        "owner": OWNERS["maya"],
        "tools": ["Web Search", "Web Fetch"],
        "system_prompt": (
            "You are a competitive intelligence researcher for an early-stage SaaS company.\n"
            "Pull the last 14 days of public material from named competitors (changelogs, blog posts, pricing pages).\n"
            "Identify positioning shifts, new features, and pricing changes — ignore vanity launches.\n"
            "Cross-check claims across at least two sources before listing them as confirmed.\n"
            "Output a structured brief with four sections: summary, competitors, pricing_changes, recommendation.\n"
            "Be skeptical: if a 'launch' is just a marketing post with no docs, mark it as low-confidence."
        ),
        "done_when": "Structured brief returned with all four sections populated; pricing_changes citations resolve.",
        "stats": {"runs_per_week": 412, "rating": 4.7, "rating_count": 32, "fork_count": 4},
        "version_history": [
            {"version": 3, "date": "2026-04-22", "note": "Added pricing_changes section + cross-source check."},
            {"version": 2, "date": "2026-03-30", "note": "Tightened summary length; removed 'sentiment analysis' (noisy)."},
            {"version": 1, "date": "2026-03-12", "note": "Initial version."},
        ],
        "forks": [
            {"name": "competitive-researcher-fintech", "owner": "Sam P.", "version": 1},
            {"name": "competitive-researcher-devtools", "owner": "Jake G.", "version": 2},
            {"name": "ai-coding-tools-watch", "owner": "Maya M.", "version": 1},
            {"name": "weekly-positioning-digest", "owner": "Sam P.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Maya M.",  "when": "12 min ago", "cost": 0.32, "duration_sec": 24, "outcome": "completed"},
            {"who": "Jake G.",  "when": "2 hr ago",   "cost": 0.41, "duration_sec": 31, "outcome": "completed"},
            {"who": "Sam P.",   "when": "yesterday",  "cost": 0.29, "duration_sec": 22, "outcome": "completed"},
        ],
        "mock_test_run": COMPETITIVE_RESEARCHER_RUN,
    },

    "pr-reviewer": {
        "id": "pr-reviewer",
        "name": "pr-reviewer",
        "description": "Reviews open PRs against codebase conventions and flags risks.",
        "version": 2,
        "category": "Engineering",
        "created_at": "2026-02-04",
        "owner": OWNERS["jake"],
        "tools": ["Filesystem", "Bash", "Git"],
        "system_prompt": (
            "You are a senior reviewer for the ravi-hq/fairy codebase.\n"
            "Read the diff in full, then read the surrounding files for each touched function.\n"
            "Categorize findings into Bugs, Style, Security, and Performance — never lump them together.\n"
            "Flag any change that bypasses select_for_update on session state, weakens a version check,\n"
            "or modifies an endpoint's response shape without updating docs/openapi.yaml.\n"
            "Be direct. Say 'request-changes' or 'approve' — no 'looks mostly good'.\n"
            "End with `must_fix_before_merge` listing the blocking findings only."
        ),
        "done_when": "Review posted with Bugs / Style / Security / Performance categories and an explicit verdict.",
        "stats": {"runs_per_week": 1204, "rating": 4.9, "rating_count": 88, "fork_count": 12},
        "version_history": [
            {"version": 2, "date": "2026-04-15", "note": "Added must_fix_before_merge field; tightened verdict wording."},
            {"version": 1, "date": "2026-02-04", "note": "Initial version."},
        ],
        "forks": [
            {"name": "frontend-pr-reviewer", "owner": "Sam P.", "version": 3},
            {"name": "migration-pr-reviewer", "owner": "Jake G.", "version": 1},
            {"name": "security-pr-reviewer", "owner": "Maya M.", "version": 2},
            {"name": "docs-pr-reviewer", "owner": "Sam P.", "version": 1},
            {"name": "api-shape-reviewer", "owner": "Jake G.", "version": 1},
            {"name": "test-coverage-reviewer", "owner": "Maya M.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Jake G.",  "when": "4 min ago",  "cost": 0.18, "duration_sec": 14, "outcome": "completed"},
            {"who": "Sam P.",   "when": "22 min ago", "cost": 0.21, "duration_sec": 17, "outcome": "completed"},
            {"who": "Maya M.",  "when": "1 hr ago",   "cost": 0.19, "duration_sec": 15, "outcome": "completed"},
        ],
        "mock_test_run": PR_REVIEWER_RUN,
    },

    "on-call-scout": {
        "id": "on-call-scout",
        "name": "on-call-scout",
        "description": "First responder for incidents — runs the investigation playbook before paging.",
        "version": 1,
        "category": "Ops",
        "created_at": "2026-04-08",
        "owner": OWNERS["maya"],
        "tools": ["Honeycomb", "GitHub", "Slack"],
        "system_prompt": (
            "You are the on-call scout. When paged, run the investigation playbook *before* paging a human.\n"
            "Step 1: query Honeycomb for the alerted metric, last 30 minutes; identify the contributor via BubbleUp.\n"
            "Step 2: search GitHub for merges in the last hour that touched the suspected component.\n"
            "Step 3: form a single hypothesis with a confidence label (low / medium / high).\n"
            "Only set page_human=true if confidence is low OR if the suspected fix is a revert of a Friday-evening deploy.\n"
            "File the brief in #on-call as a thread; never DM."
        ),
        "done_when": "Structured brief filed in #on-call with hypothesis, evidence, suggested_action, and page_human flag.",
        "stats": {"runs_per_week": 47, "rating": 4.4, "rating_count": 11, "fork_count": 2},
        "version_history": [
            {"version": 1, "date": "2026-04-08", "note": "Initial version."},
        ],
        "forks": [
            {"name": "on-call-scout-frontend", "owner": "Sam P.", "version": 1},
            {"name": "on-call-scout-payments", "owner": "Jake G.", "version": 1},
        ],
        "recent_runs": [
            {"who": "PagerDuty", "when": "yesterday",  "cost": 0.27, "duration_sec": 19, "outcome": "completed"},
            {"who": "Maya M.",   "when": "2 days ago", "cost": 0.24, "duration_sec": 16, "outcome": "completed"},
        ],
        "mock_test_run": ON_CALL_SCOUT_RUN,
    },

    "changelog-writer": {
        "id": "changelog-writer",
        "name": "changelog-writer",
        "description": "Turns a git log range into a customer-friendly changelog.",
        "version": 2,
        "category": "Engineering",
        "created_at": "2026-02-22",
        "owner": OWNERS["sam"],
        "tools": ["Git", "Filesystem"],
        "system_prompt": (
            "You write changelogs for ravi-hq/fairy.\n"
            "Given a git range, produce three artifacts: a customer-facing markdown post, an engineering-internal log,\n"
            "and a top-of-file entry for CHANGELOG.md (Keep-a-Changelog format).\n"
            "Customer-facing posts lead with the why, never with the commit hash.\n"
            "Internal logs cite PR numbers and group by subsystem.\n"
            "If the range contains zero user-visible changes, say so plainly and skip the customer post."
        ),
        "done_when": "Three markdown files emitted at expected paths; CHANGELOG.md top entry follows Keep-a-Changelog.",
        "stats": {"runs_per_week": 89, "rating": 4.6, "rating_count": 19, "fork_count": 7},
        "version_history": [
            {"version": 2, "date": "2026-04-02", "note": "Drop the customer post if no user-visible changes."},
            {"version": 1, "date": "2026-02-22", "note": "Initial version."},
        ],
        "forks": [
            {"name": "release-notes-marketing", "owner": "Maya M.", "version": 2},
            {"name": "weekly-eng-digest", "owner": "Jake G.", "version": 1},
            {"name": "sdk-changelog-py", "owner": "Sam P.", "version": 1},
            {"name": "sdk-changelog-ts", "owner": "Sam P.", "version": 1},
            {"name": "breaking-changes-only", "owner": "Jake G.", "version": 1},
            {"name": "patch-release-notes", "owner": "Maya M.", "version": 1},
            {"name": "investor-monthly-update", "owner": "Sam P.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Sam P.",  "when": "30 min ago", "cost": 0.22, "duration_sec": 18, "outcome": "completed"},
            {"who": "Jake G.", "when": "3 hr ago",   "cost": 0.20, "duration_sec": 16, "outcome": "completed"},
        ],
        "mock_test_run": CHANGELOG_WRITER_RUN,
    },

    "spec-drafter": {
        "id": "spec-drafter",
        "name": "spec-drafter",
        "description": "Drafts engineering specs from a one-line prompt + linked tickets.",
        "version": 1,
        "category": "Engineering",
        "created_at": "2026-04-18",
        "owner": OWNERS["sam"],
        "tools": ["Web Fetch", "Linear", "Notion"],
        "system_prompt": (
            "You draft engineering specs in the standard 7-section format:\n"
            "Background / Goals / Non-goals / Design / Risks / Rollout / Open Questions.\n"
            "Read every linked Linear ticket fully before writing — do not paraphrase from the title alone.\n"
            "When the prompt is ambiguous, list the ambiguity in Open Questions rather than guessing.\n"
            "Risks must be specific (e.g., 'index bloat on agent_session_state'), not vague ('performance')."
        ),
        "done_when": "Spec doc created in Notion with all 7 sections present and at least 1 entry per section.",
        "stats": {"runs_per_week": 22, "rating": 4.2, "rating_count": 6, "fork_count": 1},
        "version_history": [
            {"version": 1, "date": "2026-04-18", "note": "Initial version."},
        ],
        "forks": [
            {"name": "rfc-drafter", "owner": "Maya M.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Sam P.", "when": "1 day ago",  "cost": 0.51, "duration_sec": 42, "outcome": "completed"},
            {"who": "Maya M.", "when": "3 days ago", "cost": 0.48, "duration_sec": 39, "outcome": "completed"},
        ],
        "mock_test_run": SPEC_DRAFTER_RUN,
    },

    "dependency-bumper": {
        "id": "dependency-bumper",
        "name": "dependency-bumper",
        "description": "Bumps a dependency, runs tests, opens a PR if they pass.",
        "version": 4,
        "category": "Engineering",
        "created_at": "2026-01-29",
        "owner": OWNERS["jake"],
        "tools": ["Filesystem", "Bash", "Git"],
        "system_prompt": (
            "You are a dependency bumper. Given a package + target version, produce a PR if and only if\n"
            "the test suite is green on the new version.\n"
            "Always read the upstream CHANGELOG between current and target before bumping — call out breaking changes\n"
            "in the PR description, never in a commit message.\n"
            "If `make test` fails, do not push. Exit with `tests-failed` and include the first failing test name + traceback.\n"
            "Never bypass pre-commit hooks. Never amend an existing commit."
        ),
        "done_when": "Either a PR is opened against main with green CI, or run exits with `tests-failed` and a captured traceback.",
        "stats": {"runs_per_week": 312, "rating": 4.5, "rating_count": 23, "fork_count": 3},
        "version_history": [
            {"version": 4, "date": "2026-04-25", "note": "Reads upstream CHANGELOG before the bump (was: only after test failure)."},
            {"version": 3, "date": "2026-03-18", "note": "Never amend commits; never bypass hooks."},
            {"version": 2, "date": "2026-02-11", "note": "Capture first failing test on tests-failed."},
            {"version": 1, "date": "2026-01-29", "note": "Initial version."},
        ],
        "forks": [
            {"name": "security-bumper",       "owner": "Maya M.", "version": 2},
            {"name": "node-dep-bumper",       "owner": "Sam P.", "version": 1},
            {"name": "monorepo-dep-bumper",   "owner": "Jake G.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Jake G.", "when": "8 min ago",   "cost": 0.39, "duration_sec": 67, "outcome": "completed"},
            {"who": "Renovate-bot", "when": "1 hr ago", "cost": 0.41, "duration_sec": 71, "outcome": "completed"},
            {"who": "Renovate-bot", "when": "5 hr ago", "cost": 0.40, "duration_sec": 69, "outcome": "tests-failed"},
        ],
        "mock_test_run": DEPENDENCY_BUMPER_RUN,
    },

    "onboarding-buddy": {
        "id": "onboarding-buddy",
        "name": "onboarding-buddy",
        "description": "Guides new hires through their first week — schedules intros, sends welcome packets, files reminders.",
        "version": 1,
        "category": "Growth",
        "created_at": "2026-04-19",
        "owner": OWNERS["sam"],
        "tools": ["Slack", "Notion"],
        "system_prompt": (
            "You are an onboarding concierge for a small engineering org.\n"
            "Given a new hire's name + start date + manager, build a structured first-week plan:\n"
            "intros with at least 3 teammates, a coffee chat, a codebase tour, and a Day-1 welcome DM.\n"
            "Pull from the latest 'Eng onboarding' template in Notion — never improvise the schedule from scratch.\n"
            "Use Google Calendar free/busy to pick the least-disruptive slots; never double-book.\n"
            "Always send the welcome DM as a Slack message, never as email — first contact should feel personal."
        ),
        "done_when": "All Week-1 events scheduled, welcome DM sent, onboarding doc linked, follow-up reminders queued.",
        "stats": {"runs_per_week": 38, "rating": 4.6, "rating_count": 8, "fork_count": 1},
        "version_history": [
            {"version": 1, "date": "2026-04-19", "note": "Initial version."},
        ],
        "forks": [
            {"name": "intern-onboarding-buddy", "owner": "Sam P.", "version": 1},
        ],
        "recent_runs": [
            {"who": "Sam P.",  "when": "1 hr ago",   "cost": 0.18, "duration_sec": 21, "outcome": "completed"},
            {"who": "Jake G.", "when": "2 days ago", "cost": 0.21, "duration_sec": 24, "outcome": "completed"},
        ],
        "mock_test_run": ONBOARDING_BUDDY_RUN,
    },

    "incident-comms": {
        "id": "incident-comms",
        "name": "incident-comms",
        "description": "Drafts customer-facing status updates during incidents — Statuspage + VIP Slack channels.",
        "version": 2,
        "category": "Ops",
        "created_at": "2026-03-04",
        "owner": OWNERS["maya"],
        "tools": ["Slack"],
        "system_prompt": (
            "You write customer-facing incident updates during active outages.\n"
            "Read the engineering channel for the current hypothesis and mitigation status before writing.\n"
            "Tone: factual, no internal blame, no 'we apologize for the inconvenience' filler.\n"
            "Always include: what's affected, scope (% of users / requests), current status, next-update ETA.\n"
            "Post first to Statuspage, then mirror to #customers-vip. Never DM individual customers.\n"
            "The first update must go out within 10 minutes of incident declaration — speed beats polish."
        ),
        "done_when": "Statuspage incident created with first update; #customers-vip mirror posted; next-update ETA set.",
        "stats": {"runs_per_week": 14, "rating": 4.8, "rating_count": 9, "fork_count": 0},
        "version_history": [
            {"version": 2, "date": "2026-04-12", "note": "Mirror to #customers-vip; tightened first-update template."},
            {"version": 1, "date": "2026-03-04", "note": "Initial version."},
        ],
        "forks": [],
        "recent_runs": [
            {"who": "Maya M.",   "when": "yesterday",  "cost": 0.31, "duration_sec": 27, "outcome": "completed"},
            {"who": "PagerDuty", "when": "4 days ago", "cost": 0.29, "duration_sec": 25, "outcome": "completed"},
        ],
        "mock_test_run": INCIDENT_COMMS_RUN,
    },
}


# --- Run history (per-agent) ------------------------------------------------

RUN_HISTORY: dict[str, list[dict[str, Any]]] = {
    "competitive-researcher": [
        {"id": "rh-cr-01", "ts": "14:20",       "who": "Maya M.",  "prompt": "Top 3 competitors — last 14d.",                       "duration_sec": 24, "cost": 0.32, "status": "success"},
        {"id": "rh-cr-02", "ts": "12:04",       "who": "Jake G.",  "prompt": "Pricing-page diffs since April 1.",                   "duration_sec": 31, "cost": 0.41, "status": "success"},
        {"id": "rh-cr-03", "ts": "Mon 16:42",   "who": "Sam P.",   "prompt": "What did Linear ship this week?",                     "duration_sec": 22, "cost": 0.29, "status": "success"},
        {"id": "rh-cr-04", "ts": "Mon 11:08",   "who": "Maya M.",  "prompt": "Devtools positioning shifts (Linear, Asana, Height).", "duration_sec": 26, "cost": 0.34, "status": "success"},
        {"id": "rh-cr-05", "ts": "Sun 09:15",   "who": "GitHub Action", "prompt": "Weekly digest — competitor changelogs.",          "duration_sec": 41, "cost": 0.52, "status": "success"},
        {"id": "rh-cr-06", "ts": "Fri 15:30",   "who": "Jake G.",  "prompt": "Did anyone announce AI features this week?",          "duration_sec": 19, "cost": 0.24, "status": "success"},
        {"id": "rh-cr-07", "ts": "Fri 10:01",   "who": "Sam P.",   "prompt": "Compare Asana Business vs Linear Plus pricing.",      "duration_sec": 28, "cost": 0.36, "status": "success"},
        {"id": "rh-cr-08", "ts": "Thu 14:55",   "who": "Maya M.",  "prompt": "Has Notion shipped anything in the AI space?",        "duration_sec": 17, "cost": 0.21, "status": "failed"},
        {"id": "rh-cr-09", "ts": "Wed 13:22",   "who": "Maya M.",  "prompt": "Top 3 competitors — last 14d.",                       "duration_sec": 25, "cost": 0.33, "status": "success"},
        {"id": "rh-cr-10", "ts": "Wed 09:48",   "who": "Sam P.",   "prompt": "Find any podcast appearances by competitor CEOs.",    "duration_sec": 38, "cost": 0.47, "status": "success"},
    ],
    "pr-reviewer": [
        {"id": "rh-pr-01", "ts": "14:32",       "who": "Jake G.",       "prompt": "Review PR #331 — /interrupt endpoint.",        "duration_sec": 14, "cost": 0.18, "status": "success"},
        {"id": "rh-pr-02", "ts": "13:44",       "who": "Jake G.",       "prompt": "Review PR #330 — schema diff snapshot.",        "duration_sec": 17, "cost": 0.21, "status": "success"},
        {"id": "rh-pr-03", "ts": "13:31",       "who": "GitHub Action", "prompt": "Auto-review PR #329 (renovate-bot).",           "duration_sec": 12, "cost": 0.16, "status": "success"},
        {"id": "rh-pr-04", "ts": "11:22",       "who": "Sam P.",        "prompt": "Review PR #327 — TS SDK abort listener.",       "duration_sec": 19, "cost": 0.24, "status": "success"},
        {"id": "rh-pr-05", "ts": "10:08",       "who": "Maya M.",       "prompt": "Review PR #326 — landing page narrative.",      "duration_sec": 15, "cost": 0.19, "status": "success"},
        {"id": "rh-pr-06", "ts": "Mon 17:14",   "who": "Jake G.",       "prompt": "Review PR #324 — agent_session.state filter.",  "duration_sec": 21, "cost": 0.27, "status": "success"},
        {"id": "rh-pr-07", "ts": "Mon 11:55",   "who": "GitHub Action", "prompt": "Auto-review PR #322 (deps).",                   "duration_sec": 11, "cost": 0.14, "status": "success"},
        {"id": "rh-pr-08", "ts": "Sun 22:08",   "who": "Sam P.",        "prompt": "Review PR #321 — mutation gate config.",         "duration_sec": 18, "cost": 0.22, "status": "failed"},
        {"id": "rh-pr-09", "ts": "Fri 16:40",   "who": "Maya M.",       "prompt": "Review PR #318 — provisioning refactor.",        "duration_sec": 23, "cost": 0.28, "status": "success"},
        {"id": "rh-pr-10", "ts": "Fri 09:12",   "who": "Jake G.",       "prompt": "Review PR #316 — encrypted env_vars handling.",  "duration_sec": 26, "cost": 0.32, "status": "success"},
    ],
    "on-call-scout": [
        {"id": "rh-oc-01", "ts": "13:58",     "who": "PagerDuty", "prompt": "p99 latency on /api/sessions > 4s for 8 minutes.", "duration_sec": 19, "cost": 0.27, "status": "success"},
        {"id": "rh-oc-02", "ts": "Mon 02:18", "who": "PagerDuty", "prompt": "Worker queue backlog > 500 for 12 minutes.",       "duration_sec": 22, "cost": 0.31, "status": "success"},
        {"id": "rh-oc-03", "ts": "Sun 14:09", "who": "Maya M.",   "prompt": "Investigate flaky e2e tests on main.",             "duration_sec": 16, "cost": 0.24, "status": "success"},
        {"id": "rh-oc-04", "ts": "Fri 19:22", "who": "PagerDuty", "prompt": "5xx rate on /sessions > 2% for 5 minutes.",        "duration_sec": 18, "cost": 0.26, "status": "success"},
        {"id": "rh-oc-05", "ts": "Wed 11:44", "who": "PagerDuty", "prompt": "DB connection exhaustion alert.",                  "duration_sec": 14, "cost": 0.20, "status": "failed"},
        {"id": "rh-oc-06", "ts": "Mon 21:08", "who": "Maya M.",   "prompt": "Why are session.completed events missing in PostHog?", "duration_sec": 23, "cost": 0.34, "status": "success"},
        {"id": "rh-oc-07", "ts": "Sun 09:30", "who": "PagerDuty", "prompt": "Render deploy stuck > 10 min on web service.",     "duration_sec": 17, "cost": 0.25, "status": "success"},
        {"id": "rh-oc-08", "ts": "Fri 03:42", "who": "PagerDuty", "prompt": "Procrastinate worker error rate spike.",           "duration_sec": 20, "cost": 0.28, "status": "success"},
    ],
    "changelog-writer": [
        {"id": "rh-cw-01", "ts": "14:02",     "who": "Sam P.",  "prompt": "Generate changelog for v1.4.0..v1.5.0.",           "duration_sec": 18, "cost": 0.22, "status": "success"},
        {"id": "rh-cw-02", "ts": "Mon 10:55", "who": "Jake G.", "prompt": "Generate changelog for v1.3.0..v1.4.0.",           "duration_sec": 16, "cost": 0.20, "status": "success"},
        {"id": "rh-cw-03", "ts": "Fri 14:18", "who": "Sam P.",  "prompt": "Customer-facing only — last 7 days.",              "duration_sec": 12, "cost": 0.15, "status": "success"},
        {"id": "rh-cw-04", "ts": "Wed 11:22", "who": "Maya M.", "prompt": "Engineering log for sprint 14.",                   "duration_sec": 14, "cost": 0.18, "status": "success"},
        {"id": "rh-cw-05", "ts": "Mon 16:08", "who": "Sam P.",  "prompt": "Generate changelog for v1.2.0..v1.3.0.",           "duration_sec": 17, "cost": 0.21, "status": "success"},
        {"id": "rh-cw-06", "ts": "Sun 08:11", "who": "GitHub Action", "prompt": "Auto changelog — weekly digest cron.",       "duration_sec": 22, "cost": 0.27, "status": "success"},
        {"id": "rh-cw-07", "ts": "Fri 09:45", "who": "Jake G.", "prompt": "SDK-only changelog — TS SDK fixes.",               "duration_sec": 11, "cost": 0.14, "status": "success"},
        {"id": "rh-cw-08", "ts": "Thu 13:30", "who": "Sam P.",  "prompt": "Patch release notes for v1.4.1.",                  "duration_sec": 13, "cost": 0.16, "status": "failed"},
    ],
    "spec-drafter": [
        {"id": "rh-sd-01", "ts": "13:18",     "who": "Sam P.",   "prompt": "Per-org rate limit on POST /sessions.",             "duration_sec": 42, "cost": 0.51, "status": "success"},
        {"id": "rh-sd-02", "ts": "Mon 15:42", "who": "Maya M.",  "prompt": "Spec out the auto-revert workflow.",                "duration_sec": 39, "cost": 0.48, "status": "success"},
        {"id": "rh-sd-03", "ts": "Fri 11:08", "who": "Jake G.",  "prompt": "Sprite cold-start budget — design doc.",            "duration_sec": 44, "cost": 0.54, "status": "success"},
        {"id": "rh-sd-04", "ts": "Wed 14:22", "who": "Sam P.",   "prompt": "Multi-region sprite placement.",                    "duration_sec": 51, "cost": 0.62, "status": "success"},
        {"id": "rh-sd-05", "ts": "Mon 10:18", "who": "Maya M.",  "prompt": "RFC: deprecate session.recent_events shape.",       "duration_sec": 38, "cost": 0.47, "status": "success"},
        {"id": "rh-sd-06", "ts": "Sun 19:30", "who": "Jake G.",  "prompt": "Spec out idempotency keys on POST /agents.",        "duration_sec": 36, "cost": 0.45, "status": "success"},
        {"id": "rh-sd-07", "ts": "Fri 16:55", "who": "Sam P.",   "prompt": "Auto-revert UX — internal vs external.",            "duration_sec": 40, "cost": 0.49, "status": "failed"},
        {"id": "rh-sd-08", "ts": "Thu 09:12", "who": "Maya M.",  "prompt": "Telemetry redaction policy.",                       "duration_sec": 42, "cost": 0.51, "status": "success"},
    ],
    "dependency-bumper": [
        {"id": "rh-db-01", "ts": "12:51",     "who": "Jake G.",       "prompt": "Bump django 5.0.4 -> 5.1.2.",        "duration_sec": 67, "cost": 0.39, "status": "success"},
        {"id": "rh-db-02", "ts": "11:30",     "who": "Renovate-bot",  "prompt": "Bump pydantic 2.5.3 -> 2.6.0.",       "duration_sec": 71, "cost": 0.41, "status": "success"},
        {"id": "rh-db-03", "ts": "08:15",     "who": "Renovate-bot",  "prompt": "Bump procrastinate 2.9.1 -> 2.10.0.", "duration_sec": 69, "cost": 0.40, "status": "failed"},
        {"id": "rh-db-04", "ts": "Mon 22:18", "who": "Renovate-bot",  "prompt": "Bump fastapi 0.110.0 -> 0.111.0.",    "duration_sec": 64, "cost": 0.37, "status": "success"},
        {"id": "rh-db-05", "ts": "Mon 14:08", "who": "Jake G.",       "prompt": "Bump httpx 0.27.0 -> 0.28.1.",        "duration_sec": 58, "cost": 0.34, "status": "success"},
        {"id": "rh-db-06", "ts": "Sun 03:24", "who": "Renovate-bot",  "prompt": "Bump uvicorn 0.29.0 -> 0.30.0.",      "duration_sec": 62, "cost": 0.36, "status": "success"},
        {"id": "rh-db-07", "ts": "Fri 18:42", "who": "Renovate-bot",  "prompt": "Bump cryptography 42.0.5 -> 42.0.7.", "duration_sec": 71, "cost": 0.41, "status": "failed"},
        {"id": "rh-db-08", "ts": "Wed 12:11", "who": "Jake G.",       "prompt": "Bump psycopg 3.1.18 -> 3.1.19.",      "duration_sec": 68, "cost": 0.40, "status": "success"},
    ],
    "onboarding-buddy": [
        {"id": "rh-ob-01", "ts": "Mon 09:02", "who": "Sam P.",  "prompt": "Onboard Priya R. — Eng, starts Mon, manager Jake.", "duration_sec": 21, "cost": 0.18, "status": "success"},
        {"id": "rh-ob-02", "ts": "Fri 14:30", "who": "Jake G.", "prompt": "Onboard Tomás L. — Eng intern.",                    "duration_sec": 24, "cost": 0.21, "status": "success"},
        {"id": "rh-ob-03", "ts": "Wed 10:11", "who": "Sam P.",  "prompt": "Refresh week-1 plan for Avery (already started).", "duration_sec": 19, "cost": 0.16, "status": "success"},
        {"id": "rh-ob-04", "ts": "Mon 11:45", "who": "Maya M.", "prompt": "Onboard Dani W. — Design.",                        "duration_sec": 22, "cost": 0.19, "status": "success"},
        {"id": "rh-ob-05", "ts": "Sun 16:08", "who": "Sam P.",  "prompt": "Onboard Kai O. — Ops.",                            "duration_sec": 20, "cost": 0.17, "status": "success"},
        {"id": "rh-ob-06", "ts": "Fri 09:22", "who": "Jake G.", "prompt": "Generate intern-onboarding-buddy fork plan.",      "duration_sec": 26, "cost": 0.22, "status": "failed"},
        {"id": "rh-ob-07", "ts": "Wed 14:03", "who": "Sam P.",  "prompt": "Onboard Riley K. — Engineering.",                  "duration_sec": 23, "cost": 0.20, "status": "success"},
        {"id": "rh-ob-08", "ts": "Mon 13:50", "who": "Sam P.",  "prompt": "Reschedule week-1 — Priya delayed by 2 days.",     "duration_sec": 18, "cost": 0.15, "status": "success"},
    ],
    "incident-comms": [
        {"id": "rh-ic-01", "ts": "13:59",     "who": "Maya M.",   "prompt": "First update for INC-2026-04-29-01.",          "duration_sec": 27, "cost": 0.31, "status": "success"},
        {"id": "rh-ic-02", "ts": "Mon 02:24", "who": "PagerDuty", "prompt": "Worker queue backlog — first update.",          "duration_sec": 29, "cost": 0.34, "status": "success"},
        {"id": "rh-ic-03", "ts": "Fri 19:30", "who": "PagerDuty", "prompt": "5xx spike — first update + status update.",     "duration_sec": 31, "cost": 0.36, "status": "success"},
        {"id": "rh-ic-04", "ts": "Wed 11:50", "who": "Maya M.",   "prompt": "DB connection alert — customer-facing comm.",   "duration_sec": 25, "cost": 0.29, "status": "success"},
        {"id": "rh-ic-05", "ts": "Mon 21:15", "who": "Maya M.",   "prompt": "Telemetry gap — internal-only comm.",           "duration_sec": 22, "cost": 0.26, "status": "success"},
        {"id": "rh-ic-06", "ts": "Sun 09:35", "who": "Maya M.",   "prompt": "Render deploy stuck — status update only.",     "duration_sec": 19, "cost": 0.23, "status": "failed"},
        {"id": "rh-ic-07", "ts": "Fri 03:48", "who": "PagerDuty", "prompt": "Procrastinate spike — full incident comms.",    "duration_sec": 33, "cost": 0.39, "status": "success"},
    ],
}


# --- Daily spend (14 days) ---------------------------------------------------
# Each entry: { "date": "YYYY-MM-DD", "by_agent": { agent_id: dollars } }

DAILY_SPEND: list[dict[str, Any]] = [
    {"date": "2026-04-16", "by_agent": {"pr-reviewer": 31.40, "competitive-researcher": 18.20, "dependency-bumper": 14.10, "changelog-writer": 2.40, "on-call-scout": 1.10, "spec-drafter": 1.50, "onboarding-buddy": 0.40, "incident-comms": 0.60}},
    {"date": "2026-04-17", "by_agent": {"pr-reviewer": 28.10, "competitive-researcher": 21.00, "dependency-bumper": 17.80, "changelog-writer": 3.10, "on-call-scout": 1.60, "spec-drafter": 1.80, "onboarding-buddy": 0.50, "incident-comms": 0.70}},
    {"date": "2026-04-18", "by_agent": {"pr-reviewer": 22.40, "competitive-researcher": 14.50, "dependency-bumper": 11.20, "changelog-writer": 1.80, "on-call-scout": 0.80, "spec-drafter": 1.10, "onboarding-buddy": 0.30, "incident-comms": 0.40}},
    {"date": "2026-04-19", "by_agent": {"pr-reviewer": 19.80, "competitive-researcher": 12.30, "dependency-bumper": 10.40, "changelog-writer": 1.60, "on-call-scout": 0.90, "spec-drafter": 1.00, "onboarding-buddy": 0.40, "incident-comms": 0.50}},
    {"date": "2026-04-20", "by_agent": {"pr-reviewer": 33.20, "competitive-researcher": 19.10, "dependency-bumper": 16.80, "changelog-writer": 2.90, "on-call-scout": 1.40, "spec-drafter": 1.70, "onboarding-buddy": 0.60, "incident-comms": 0.80}},
    {"date": "2026-04-21", "by_agent": {"pr-reviewer": 35.60, "competitive-researcher": 20.40, "dependency-bumper": 18.10, "changelog-writer": 3.20, "on-call-scout": 1.80, "spec-drafter": 1.90, "onboarding-buddy": 0.70, "incident-comms": 0.90}},
    {"date": "2026-04-22", "by_agent": {"pr-reviewer": 36.80, "competitive-researcher": 19.80, "dependency-bumper": 17.40, "changelog-writer": 2.80, "on-call-scout": 1.50, "spec-drafter": 1.60, "onboarding-buddy": 0.60, "incident-comms": 0.80}},
    {"date": "2026-04-23", "by_agent": {"pr-reviewer": 34.10, "competitive-researcher": 21.20, "dependency-bumper": 18.60, "changelog-writer": 3.10, "on-call-scout": 1.70, "spec-drafter": 1.80, "onboarding-buddy": 0.70, "incident-comms": 0.90}},
    {"date": "2026-04-24", "by_agent": {"pr-reviewer": 38.20, "competitive-researcher": 22.10, "dependency-bumper": 19.40, "changelog-writer": 3.40, "on-call-scout": 2.10, "spec-drafter": 2.00, "onboarding-buddy": 0.80, "incident-comms": 1.10}},
    {"date": "2026-04-25", "by_agent": {"pr-reviewer": 26.10, "competitive-researcher": 14.20, "dependency-bumper": 12.80, "changelog-writer": 1.90, "on-call-scout": 0.90, "spec-drafter": 1.10, "onboarding-buddy": 0.40, "incident-comms": 0.50}},
    {"date": "2026-04-26", "by_agent": {"pr-reviewer": 24.40, "competitive-researcher": 13.50, "dependency-bumper": 11.60, "changelog-writer": 1.70, "on-call-scout": 0.80, "spec-drafter": 1.00, "onboarding-buddy": 0.30, "incident-comms": 0.40}},
    {"date": "2026-04-27", "by_agent": {"pr-reviewer": 41.20, "competitive-researcher": 23.80, "dependency-bumper": 20.40, "changelog-writer": 3.60, "on-call-scout": 2.30, "spec-drafter": 2.10, "onboarding-buddy": 0.90, "incident-comms": 1.20}},
    {"date": "2026-04-28", "by_agent": {"pr-reviewer": 39.80, "competitive-researcher": 22.40, "dependency-bumper": 19.10, "changelog-writer": 3.30, "on-call-scout": 2.00, "spec-drafter": 1.90, "onboarding-buddy": 0.80, "incident-comms": 1.00}},
    {"date": "2026-04-29", "by_agent": {"pr-reviewer": 28.20, "competitive-researcher": 16.10, "dependency-bumper": 13.80, "changelog-writer": 2.40, "on-call-scout": 1.40, "spec-drafter": 1.40, "onboarding-buddy": 0.60, "incident-comms": 0.80}},
]


# --- Tool catalog -----------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {
    "Web Search": {
        "name": "Web Search",
        "description": "Searches the public web. Returns titles, URLs, and snippets.",
        "sensitivity": "Safe",
        "example": "search('Linear changelog April 2026')",
        "icon": "search",
    },
    "Web Fetch": {
        "name": "Web Fetch",
        "description": "Fetches the rendered content of a single URL — text + visible markup.",
        "sensitivity": "Safe",
        "example": "fetch('https://linear.app/changelog')",
        "icon": "globe",
    },
    "Filesystem": {
        "name": "Filesystem",
        "description": "Read and write files inside the Sprite's workspace. Scoped to /workspace.",
        "sensitivity": "Sensitive",
        "example": "read('src/views/sessions.py'), write('CHANGELOG.md', ...)",
        "icon": "folder",
    },
    "Bash": {
        "name": "Bash",
        "description": "Run shell commands inside the Sprite. No host access; sandboxed VM.",
        "sensitivity": "Sensitive",
        "example": "bash('pytest tests/test_sessions.py -k interrupt -q')",
        "icon": "terminal",
    },
    "Git": {
        "name": "Git",
        "description": "Local git operations — branch, diff, commit, push. No force-push.",
        "sensitivity": "Sensitive",
        "example": "git.commit(-am 'chore: bump deps'), git.push(origin)",
        "icon": "git",
    },
    "GitHub": {
        "name": "GitHub",
        "description": "Read/write the GitHub API: PRs, issues, search, code review comments.",
        "sensitivity": "Safe",
        "example": "github.search('repo:ravi-hq/fairy state filter merged:>2026-04-25')",
        "icon": "github",
    },
    "Honeycomb": {
        "name": "Honeycomb",
        "description": "Run Honeycomb queries, BubbleUp pivots, and read triggers/SLOs.",
        "sensitivity": "Safe",
        "example": "honeycomb.query(dataset='fairy-prod', calc='HEATMAP(duration_ms)')",
        "icon": "chart",
    },
    "Slack": {
        "name": "Slack",
        "description": "Read channel history, post messages, DM users, look up identities.",
        "sensitivity": "Safe",
        "example": "slack.post(channel='#on-call', type='incident_brief')",
        "icon": "slack",
    },
    "Linear": {
        "name": "Linear",
        "description": "Read/write Linear: tickets, projects, comments, attachments.",
        "sensitivity": "Safe",
        "example": "linear.fetch('ENG-412'), linear.comment('ENG-412', body=...)",
        "icon": "linear",
    },
    "Notion": {
        "name": "Notion",
        "description": "Search Notion databases, create pages, update structured fields.",
        "sensitivity": "Safe",
        "example": "notion.create_page(db='engineering-specs', title='Spec: ...')",
        "icon": "notion",
    },
}


# --- Audit data --------------------------------------------------------------

AUDIT: dict[str, Any] = {
    "window": "this week (2026-04-22 — 2026-04-29)",
    "total_runs": 2247,
    "total_cost_usd": 638.80,
    "by_agent": [
        {"agent": "pr-reviewer",            "runs": 1204, "cost": 244.81},
        {"agent": "competitive-researcher", "runs": 412,  "cost": 137.21},
        {"agent": "dependency-bumper",      "runs": 312,  "cost": 124.80},
        {"agent": "changelog-writer",       "runs": 89,   "cost": 19.58},
        {"agent": "on-call-scout",          "runs": 47,   "cost": 12.69},
        {"agent": "spec-drafter",           "runs": 22,   "cost": 11.22},
        {"agent": "onboarding-buddy",       "runs": 38,   "cost":  7.20},
        {"agent": "incident-comms",         "runs": 14,   "cost":  6.40},
    ],
    "by_human": [
        {"who": "Jake G.",       "runs": 612, "cost": 198.40},
        {"who": "Sam P.",        "runs": 388, "cost": 142.20},
        {"who": "Maya M.",       "runs": 304, "cost": 122.80},
        {"who": "Renovate-bot",  "runs": 268, "cost": 107.20},
        {"who": "PagerDuty",     "runs": 41,  "cost":  11.00},
        {"who": "GitHub Action", "runs": 473, "cost":  30.80},
    ],
    "recent_activity": [
        {"ts": "14:32", "who": "Jake G.",       "agent": "pr-reviewer",            "cost": 0.18, "duration_sec": 14, "outcome": "completed"},
        {"ts": "14:20", "who": "Maya M.",       "agent": "competitive-researcher", "cost": 0.32, "duration_sec": 24, "outcome": "completed"},
        {"ts": "14:11", "who": "Renovate-bot",  "agent": "dependency-bumper",      "cost": 0.40, "duration_sec": 69, "outcome": "tests-failed"},
        {"ts": "14:02", "who": "Sam P.",        "agent": "changelog-writer",       "cost": 0.22, "duration_sec": 18, "outcome": "completed"},
        {"ts": "13:58", "who": "PagerDuty",     "agent": "on-call-scout",          "cost": 0.27, "duration_sec": 19, "outcome": "completed"},
        {"ts": "13:44", "who": "Jake G.",       "agent": "pr-reviewer",            "cost": 0.21, "duration_sec": 17, "outcome": "completed"},
        {"ts": "13:31", "who": "GitHub Action", "agent": "pr-reviewer",            "cost": 0.16, "duration_sec": 12, "outcome": "completed"},
        {"ts": "13:18", "who": "Sam P.",        "agent": "spec-drafter",           "cost": 0.51, "duration_sec": 42, "outcome": "completed"},
        {"ts": "13:04", "who": "Maya M.",       "agent": "competitive-researcher", "cost": 0.29, "duration_sec": 22, "outcome": "completed"},
        {"ts": "12:51", "who": "Jake G.",       "agent": "dependency-bumper",      "cost": 0.39, "duration_sec": 67, "outcome": "completed"},
    ],
}


def list_agents_summary() -> list[dict[str, Any]]:
    """Return library-card-shaped summaries (no system_prompt or full mock_test_run)."""
    out = []
    for agent in AGENTS.values():
        out.append({
            "id": agent["id"],
            "name": agent["name"],
            "description": agent["description"],
            "version": agent["version"],
            "category": agent.get("category", "Other"),
            "created_at": agent.get("created_at", "2026-01-01"),
            "owner": agent["owner"],
            "tools": agent["tools"],
            "stats": agent["stats"],
        })
    return out


def get_agent(agent_id: str) -> dict[str, Any] | None:
    agent = AGENTS.get(agent_id)
    if agent is None:
        return None
    return {k: v for k, v in agent.items() if k != "mock_test_run"}


def get_test_run(agent_id: str) -> list[tuple[float, str, str]] | None:
    agent = AGENTS.get(agent_id)
    if agent is None:
        return None
    return agent.get("mock_test_run") or _generic_test_run(agent_id)


def _generic_test_run(agent_id: str) -> list[tuple[float, str, str]]:
    """Fallback test sequence for newly-created agents that have no hand-written run."""
    agent = AGENTS.get(agent_id, {})
    name = agent.get("name", agent_id)
    tools = agent.get("tools") or []
    tool_summary = ", ".join(tools) if tools else "no external tools"
    return [
        (0.3, "stage",   "create_sprite          claimed sprite-test in 0.16s"),
        (0.4, "stage",   f"provision_setup       reading system prompt for {name}"),
        (0.4, "stage",   f"validate_tools        {tool_summary}"),
        (0.4, "stage",   "runtime_start         claude-sonnet ready (sandbox provisioned)"),
        (0.5, "thought", "Running test prompt against the agent definition."),
        (0.7, "output",  "Test prompt processed; system prompt loaded; tool stubs responded."),
        (0.6, "output",  "Output captured. Definition looks ready to publish."),
        (0.6, "result",  _result({
            "agent": agent_id,
            "ok": True,
            "system_prompt_chars": len(agent.get("system_prompt", "") or ""),
            "tools_validated": tools,
            "note": "This is a generic test run. Hand-write a `mock_test_run` for richer streaming output.",
        })),
        (0.2, "exit", "0"),
    ]


def get_run_history(agent_id: str) -> list[dict[str, Any]]:
    """All recorded runs for an agent (most recent first). Empty for newly-created agents."""
    return RUN_HISTORY.get(agent_id, [])


def get_run_detail(agent_id: str, run_id: str) -> dict[str, Any] | None:
    """Synthesize a full run-detail payload (output, tool calls, sandbox events) for the modal."""
    rows = RUN_HISTORY.get(agent_id, [])
    row = next((r for r in rows if r["id"] == run_id), None)
    if row is None:
        return None
    agent = AGENTS.get(agent_id, {})
    seq = agent.get("mock_test_run") or _generic_test_run(agent_id)
    tool_calls = [t for (_, k, t) in seq if k == "tool"]
    sandbox = [t for (_, k, t) in seq if k == "stage"]
    output_lines = [t for (_, k, t) in seq if k in ("thought", "output")]
    result_text = next((t for (_, k, t) in seq if k == "result"), "")
    output_full = (
        "\n".join(output_lines)
        + ("\n\n--- result ---\n" + result_text if result_text else "")
    )
    return {
        **row,
        "agent_id": agent_id,
        "agent_name": agent.get("name", agent_id),
        "prompt_full": (
            row["prompt"]
            + "\n\n(Context: this run was triggered by "
            + row["who"]
            + " at "
            + row["ts"]
            + ". Full transcript reconstructed from the agent's last published definition.)"
        ),
        "output_full": output_full,
        "tool_calls": tool_calls,
        "sandbox_events": sandbox,
    }


def get_audit_kpis() -> dict[str, Any]:
    """4 KPI cards above the spend chart."""
    by_agent = AUDIT["by_agent"]
    top = max(by_agent, key=lambda x: x["cost"])
    avg_dur = 28  # weighted-ish average from RUN_HISTORY rows; rounded for display
    return {
        "total_runs": AUDIT["total_runs"],
        "total_spend": AUDIT["total_cost_usd"],
        "avg_duration_sec": avg_dur,
        "top_agent": {"name": top["agent"], "cost": top["cost"]},
    }


def get_footer_stats() -> dict[str, Any]:
    """Footer-strip stats. 'spent' is the most recent day, 'forks' is total fork_count."""
    today = DAILY_SPEND[-1]
    today_total = round(sum(today["by_agent"].values()), 2)
    total_forks = sum(a["stats"]["fork_count"] for a in AGENTS.values())
    return {
        "agent_count": len(AGENTS),
        "runs_this_week": AUDIT["total_runs"],
        "spent_today": today_total,
        "fork_count": total_forks,
    }


def add_agent(record: dict[str, Any]) -> dict[str, Any]:
    """Insert a freshly-published agent into the in-memory library."""
    AGENTS[record["id"]] = record
    return record
