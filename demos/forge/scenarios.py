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
}


# --- Audit data --------------------------------------------------------------

AUDIT: dict[str, Any] = {
    "window": "this week (2026-04-22 — 2026-04-29)",
    "total_runs": 2086,
    "total_cost_usd": 612.40,
    "by_agent": [
        {"agent": "pr-reviewer",            "runs": 1204, "cost": 244.81},
        {"agent": "competitive-researcher", "runs": 412,  "cost": 137.21},
        {"agent": "dependency-bumper",      "runs": 312,  "cost": 124.80},
        {"agent": "changelog-writer",       "runs": 89,   "cost": 19.58},
        {"agent": "on-call-scout",          "runs": 47,   "cost": 12.69},
        {"agent": "spec-drafter",           "runs": 22,   "cost": 11.22},
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
    return agent["mock_test_run"]
